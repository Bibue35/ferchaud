"""
VPIN — Volume-Synchronized Probability of Informed Trading
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detects toxic order flow (informed traders) by analysing volume-bucketed
trade imbalances. When VPIN is high, spreads widen and adverse selection
risk increases — the bot should reduce exposure.

Algorithm:
  1. Accumulate trades into fixed-volume buckets (e.g., 2% of 20d ADV).
  2. For each bucket, classify volume as buy/sell using Bulk Volume
     Classification (BVC): V_buy = V * Phi(Z) where Z = (close-open)/sigma.
  3. VPIN = avg(|V_sell - V_buy|) / V over a rolling window of n buckets.
  4. Rank current VPIN against its own history to produce a CDF percentile.

Provides:
  - `is_toxic(symbol)` → True when VPIN CDF exceeds threshold (default 85th pct)
  - `get_vpin(symbol)` → raw VPIN float
  - `get_vpin_cdf(symbol)` → CDF percentile [0, 1]
"""
from collections import deque
from typing import Dict, Optional, Tuple

import numpy as np
from scipy.stats import norm

from config import CONFIG
from data.feed import MarketDataFeed
from utils.logger import get_logger

log = get_logger("core.vpin")


class _SymbolVPIN:
    """Per-symbol VPIN state machine."""

    def __init__(self, bucket_volume: float, window: int = 50) -> None:
        self._bucket_vol = max(bucket_volume, 1.0)
        self._window = window

        # Accumulator for current bucket
        self._cur_buy: float = 0.0
        self._cur_sell: float = 0.0
        self._cur_vol: float = 0.0

        # Rolling buckets
        self._buckets: deque = deque(maxlen=window)
        # History for CDF
        self._history: deque = deque(maxlen=2000)

        self._sigma: float = 0.01  # running estimate of price-change std

    def add_bar(self, open_: float, close: float, volume: float) -> None:
        """Process a single OHLC bar and accumulate into volume buckets."""
        if volume <= 0 or open_ <= 0:
            return

        # BVC classification
        delta_p = close - open_
        self._update_sigma(delta_p)
        z = delta_p / self._sigma if self._sigma > 1e-10 else 0.0
        buy_frac = norm.cdf(z)
        buy_vol = volume * buy_frac
        sell_vol = volume * (1 - buy_frac)

        remaining_buy = buy_vol
        remaining_sell = sell_vol
        remaining_total = volume

        while remaining_total > 0:
            space = self._bucket_vol - self._cur_vol
            fill = min(remaining_total, space)
            ratio = fill / volume if volume > 0 else 0.5

            self._cur_buy += remaining_buy * ratio
            self._cur_sell += remaining_sell * ratio
            self._cur_vol += fill
            remaining_buy -= remaining_buy * ratio
            remaining_sell -= remaining_sell * ratio
            remaining_total -= fill

            if self._cur_vol >= self._bucket_vol * 0.999:
                # Bucket complete
                imbalance = abs(self._cur_sell - self._cur_buy)
                self._buckets.append(imbalance)
                self._cur_buy = 0.0
                self._cur_sell = 0.0
                self._cur_vol = 0.0

    def _update_sigma(self, delta_p: float) -> None:
        """EWMA estimate of price-change standard deviation."""
        lam = 0.94
        self._sigma = np.sqrt(lam * self._sigma ** 2 + (1 - lam) * delta_p ** 2)

    @property
    def vpin(self) -> Optional[float]:
        if len(self._buckets) < self._window:
            return None
        v = sum(self._buckets) / (self._window * self._bucket_vol)
        self._history.append(v)
        return v

    @property
    def vpin_cdf(self) -> Optional[float]:
        v = self.vpin
        if v is None or len(self._history) < 30:
            return None
        return float(np.mean(np.array(self._history) <= v))


class VPINMonitor:
    """Tracks VPIN across all symbols."""

    def __init__(self, feed: MarketDataFeed) -> None:
        self._feed = feed
        self._trackers: Dict[str, _SymbolVPIN] = {}

    def initialize(self, symbol: str) -> None:
        """Bootstrap VPIN with historical 5-min bars."""
        df = self._feed.get_bars(symbol, timeframe="5Min", limit=1000)
        if df.empty:
            log.warning("No data to initialise VPIN for %s", symbol)
            return

        # Estimate average daily volume
        daily = self._feed.get_bars(symbol, timeframe="1D", limit=20)
        if daily.empty:
            adv = df["volume"].mean() * 78  # ~78 5-min bars per day
        else:
            adv = daily["volume"].mean()

        bucket_vol = adv * CONFIG.vpin_bucket_fraction
        tracker = _SymbolVPIN(bucket_vol, CONFIG.vpin_window)

        for _, bar in df.iterrows():
            tracker.add_bar(bar["open"], bar["close"], bar["volume"])

        self._trackers[symbol] = tracker
        v = tracker.vpin
        cdf = tracker.vpin_cdf
        log.info(
            "VPIN initialised for %s — bucket_vol=%.0f  VPIN=%s  CDF=%s",
            symbol,
            bucket_vol,
            f"{v:.4f}" if v is not None else "N/A",
            f"{cdf:.2%}" if cdf is not None else "N/A",
        )

    def update(self, symbol: str, open_: float, close: float, volume: float) -> None:
        """Feed a new bar into the tracker."""
        tracker = self._trackers.get(symbol)
        if tracker is None:
            return
        tracker.add_bar(open_, close, volume)

    def refresh(self, symbol: str) -> None:
        """Pull the latest 5-min bar and feed it in."""
        df = self._feed.get_bars(symbol, timeframe="5Min", limit=2)
        if df.empty:
            return
        last = df.iloc[-1]
        self.update(symbol, last["open"], last["close"], last["volume"])

    def get_vpin(self, symbol: str) -> Optional[float]:
        t = self._trackers.get(symbol)
        return t.vpin if t else None

    def get_vpin_cdf(self, symbol: str) -> Optional[float]:
        t = self._trackers.get(symbol)
        return t.vpin_cdf if t else None

    def is_toxic(self, symbol: str) -> bool:
        cdf = self.get_vpin_cdf(symbol)
        if cdf is None:
            return False  # Not enough data — allow trading
        toxic = cdf > CONFIG.vpin_halt_threshold
        if toxic:
            log.warning(
                "VPIN TOXIC for %s — CDF=%.2f%% > threshold=%.0f%%",
                symbol, cdf * 100, CONFIG.vpin_halt_threshold * 100,
            )
        return toxic
