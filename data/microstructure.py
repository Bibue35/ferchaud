"""
Market Microstructure Signals
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lightweight signals derived from quote/trade data — no L2 needed.
Strategies can opt into these to add richer features to the learning
engine without needing a paid order-book feed.

Signals:
  • bid_ask_imbalance   — bid-side / total volume (proxy)
  • trade_aggressor     — % of recent trades hitting the ask vs bid
  • burst_detection     — Z-score of last-N-second trade count
  • micro_volatility    — realized vol over last N trades
  • price_acceleration  — second derivative of micro-price

Each signal returns a normalised score in [-1, +1] so strategies can
combine them as feature dicts for the learning engine.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import numpy as np

from utils.logger import get_logger

log = get_logger("data.microstructure")


class MicrostructureFeed:
    """
    Per-symbol rolling window of (ts, price, size, side) trades.
    Exposes derived signals on demand.
    """

    def __init__(self, window: int = 200, ttl_sec: int = 120) -> None:
        self.window = window
        self.ttl_sec = ttl_sec
        self._trades: Dict[str, Deque] = defaultdict(lambda: deque(maxlen=window))

    def add_trade(self, symbol: str, price: float, size: float,
                  side: str = "unknown", ts: Optional[float] = None) -> None:
        """side: 'buy' (aggressor took the ask) | 'sell' (took bid) | 'unknown'."""
        if ts is None:
            ts = time.time()
        self._trades[symbol].append((ts, float(price), float(size), side))

    def _recent(self, symbol: str) -> list:
        cutoff = time.time() - self.ttl_sec
        return [t for t in self._trades.get(symbol, ()) if t[0] >= cutoff]

    # ── Signals ───────────────────────────────────────────────────────────────

    def trade_aggressor(self, symbol: str) -> float:
        """
        Returns a signed score in [-1, +1]:
          +1 = all recent trades hit the ask (buy pressure)
          -1 = all recent trades hit the bid (sell pressure)
        """
        rs = self._recent(symbol)
        if not rs:
            return 0.0
        buy_vol = sum(s for _, _, s, side in rs if side == "buy")
        sell_vol = sum(s for _, _, s, side in rs if side == "sell")
        total = buy_vol + sell_vol
        if total <= 0:
            return 0.0
        return float((buy_vol - sell_vol) / total)

    def burst_score(self, symbol: str) -> float:
        """
        Z-score of last 10 seconds' trade count vs. last 60 seconds' baseline.
        > 2 = unusual burst of activity.
        """
        rs = self._recent(symbol)
        if len(rs) < 10:
            return 0.0
        now = time.time()
        last_10s = sum(1 for ts, *_ in rs if ts >= now - 10)
        last_60s = sum(1 for ts, *_ in rs if ts >= now - 60)
        if last_60s <= 0:
            return 0.0
        baseline_per_10s = last_60s / 6.0
        if baseline_per_10s <= 0:
            return 0.0
        # Approximate Poisson std as sqrt(rate)
        std = max(1.0, np.sqrt(baseline_per_10s))
        return float((last_10s - baseline_per_10s) / std)

    def micro_volatility(self, symbol: str) -> float:
        """Realized vol over recent trades (annualized rough proxy)."""
        rs = self._recent(symbol)
        if len(rs) < 10:
            return 0.0
        prices = np.array([p for _, p, _, _ in rs], dtype=float)
        if (prices > 0).all():
            log_rets = np.diff(np.log(prices))
            std = float(log_rets.std())
            return std * np.sqrt(252 * 6.5 * 60 * 60)  # rough annualization
        return 0.0

    def price_acceleration(self, symbol: str) -> float:
        """
        Sign of the second derivative of micro-price across last N trades.
        +1 = accelerating up, -1 = accelerating down.
        """
        rs = self._recent(symbol)
        if len(rs) < 5:
            return 0.0
        prices = np.array([p for _, p, _, _ in rs[-20:]], dtype=float)
        if len(prices) < 3:
            return 0.0
        d1 = np.diff(prices)
        d2 = np.diff(d1)
        return float(np.sign(d2.mean()))

    def vwap(self, symbol: str) -> float:
        """Volume-weighted average price over the window."""
        rs = self._recent(symbol)
        if not rs:
            return 0.0
        num = sum(p * s for _, p, s, _ in rs)
        den = sum(s for _, _, s, _ in rs)
        return float(num / den) if den > 0 else 0.0

    def signal_dict(self, symbol: str) -> Dict[str, float]:
        """Return all signals as a dict suitable for the learning engine."""
        return {
            "aggressor":  round(self.trade_aggressor(symbol), 3),
            "burst":      round(self.burst_score(symbol), 3),
            "micro_vol":  round(self.micro_volatility(symbol), 4),
            "accel":      round(self.price_acceleration(symbol), 1),
        }


# Process-wide singleton
_FEED: Optional[MicrostructureFeed] = None


def get_microstructure_feed() -> MicrostructureFeed:
    global _FEED
    if _FEED is None:
        _FEED = MicrostructureFeed()
    return _FEED
