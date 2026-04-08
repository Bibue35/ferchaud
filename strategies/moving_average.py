"""
Moving Average Strategy — Strategy Vault #66/67/68
Implements single MA, dual MA crossover, and triple MA confirmation.
Trend-following on individual stocks; used for swing-trade entries.
"""
from __future__ import annotations
import pandas as pd
import numpy as np


class MovingAverageStrategy:
    """
    Dual Moving Average Crossover (Vault Strategy #67)
    Short MA crosses above long MA → long signal.
    Short MA crosses below long MA → short/flat signal.
    Enhanced with volume confirmation and trailing stop.
    Standalone signal generator — no broker/executor dependencies.
    """

    name = "MovingAverageCrossover"
    description = "Dual MA crossover with volume confirmation and trailing stop."
    max_positions = 8
    position_size_pct = 0.06   # 6% per trade

    def __init__(self, short_period: int = 10, long_period: int = 50,
                 triple_period: int = 200, stop_pct: float = 0.02):
        self.short_period  = short_period
        self.long_period   = long_period
        self.triple_period = triple_period
        self.stop_pct      = stop_pct        # 2% trailing stop

    # ── Signal generation ────────────────────────────────────────────────────

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> dict:
        """
        Returns {"signal": "buy"|"sell"|"hold", "score": float, "meta": dict}
        bars: DataFrame with columns [open, high, low, close, volume], DatetimeIndex
        """
        if bars is None or len(bars) < self.long_period + 5:
            return {"signal": "hold", "score": 0.0, "meta": {}}

        close  = bars["close"].astype(float)
        volume = bars["volume"].astype(float)

        # Moving averages
        sma_short  = close.rolling(self.short_period).mean()
        sma_long   = close.rolling(self.long_period).mean()
        sma_triple = close.rolling(self.triple_period).mean() if len(close) >= self.triple_period else None

        curr_short = sma_short.iloc[-1]
        curr_long  = sma_long.iloc[-1]
        prev_short = sma_short.iloc[-2]
        prev_long  = sma_long.iloc[-2]
        price      = close.iloc[-1]

        # Volume confirmation: current volume vs 20-day average
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_avg if vol_avg > 0 else 1.0

        # Crossover detection
        golden_cross = (prev_short <= prev_long) and (curr_short > curr_long)
        death_cross  = (prev_short >= prev_long) and (curr_short < curr_long)

        # Trend strength: distance between MAs as % of price
        ma_spread_pct = abs(curr_short - curr_long) / price if price > 0 else 0

        # Triple MA filter (must be in order for strong signal)
        triple_ok = True
        if sma_triple is not None and not np.isnan(sma_triple.iloc[-1]):
            triple_val = sma_triple.iloc[-1]
            triple_ok_long  = curr_short > curr_long > triple_val
            triple_ok_short = curr_short < curr_long < triple_val
        else:
            triple_ok_long = triple_ok_short = True  # Filter disabled

        score = 0.0
        signal = "hold"

        if golden_cross and vol_ratio >= 1.2 and triple_ok_long:
            # Strong buy: crossover + volume + trend aligned
            score = min(1.0, 0.5 + ma_spread_pct * 5 + (vol_ratio - 1) * 0.1)
            signal = "buy"
        elif golden_cross and vol_ratio >= 0.8:
            # Moderate buy: crossover but weak volume
            score = 0.4
            signal = "buy"
        elif death_cross and triple_ok_short:
            # Sell / short signal
            score = -min(1.0, 0.5 + ma_spread_pct * 5)
            signal = "sell"
        elif curr_short > curr_long:
            # Already in uptrend — hold if long, don't enter
            score = 0.2
        elif curr_short < curr_long:
            score = -0.2

        return {
            "signal": signal,
            "score": round(score, 4),
            "meta": {
                "sma_short": round(float(curr_short), 4),
                "sma_long": round(float(curr_long), 4),
                "golden_cross": golden_cross,
                "death_cross": death_cross,
                "vol_ratio": round(float(vol_ratio), 2),
                "ma_spread_pct": round(float(ma_spread_pct * 100), 2),
            }
        }

    # ── Stop-loss check ──────────────────────────────────────────────────────

    def check_stop(self, entry_price: float, current_price: float,
                   direction: str = "long") -> bool:
        """Returns True if stop-loss is triggered."""
        if direction == "long":
            return current_price <= entry_price * (1 - self.stop_pct)
        else:
            return current_price >= entry_price * (1 + self.stop_pct)

    # ── EMA variant ──────────────────────────────────────────────────────────

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def generate_ema_signals(self, symbol: str, bars: pd.DataFrame) -> dict:
        """EMA crossover variant — faster response than SMA."""
        if bars is None or len(bars) < self.long_period + 5:
            return {"signal": "hold", "score": 0.0, "meta": {}}

        close = bars["close"].astype(float)
        ema_s = self.ema(close, self.short_period)
        ema_l = self.ema(close, self.long_period)

        bullish = ema_s.iloc[-1] > ema_l.iloc[-1]
        crossed_up   = ema_s.iloc[-2] <= ema_l.iloc[-2] and ema_s.iloc[-1] > ema_l.iloc[-1]
        crossed_down = ema_s.iloc[-2] >= ema_l.iloc[-2] and ema_s.iloc[-1] < ema_l.iloc[-1]

        if crossed_up:   return {"signal": "buy",  "score": 0.7, "meta": {"type": "ema_cross_up"}}
        if crossed_down: return {"signal": "sell", "score": -0.7, "meta": {"type": "ema_cross_down"}}
        if bullish:      return {"signal": "hold", "score": 0.15, "meta": {"type": "ema_uptrend"}}
        return {"signal": "hold", "score": -0.15, "meta": {"type": "ema_downtrend"}}
