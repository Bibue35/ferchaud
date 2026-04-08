"""
Donchian Channel + Support/Resistance — Vault Strategies #69 & #70
Two modes:
  1. BREAKOUT: buy when price breaks above N-day high; short when below N-day low
  2. MEAN-REVERSION: fade moves at channel extremes (useful in range-bound markets)
R² regime filter switches between modes automatically.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class DonchianChannelStrategy:
    """
    Vault Strategy #70 — Donchian Channel
    Vault Strategy #69 — Support & Resistance
    """
    name = "DonchianChannel"
    description = "Channel breakout and S/R fade with R² regime filter."
    max_positions     = 10
    position_size_pct = 0.05

    def __init__(self, channel_period: int = 20, atr_period: int = 14,
                 trend_r2_threshold: float = 0.65):
        self.channel_period      = channel_period
        self.atr_period          = atr_period
        self.trend_r2_threshold  = trend_r2_threshold   # above = trending → use breakout

    # ── ATR ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
        h, l, c = bars["high"], bars["low"], bars["close"]
        prev_c  = c.shift(1)
        tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    # ── R² trend strength ─────────────────────────────────────────────────────
    @staticmethod
    def _r_squared(prices: pd.Series, window: int = 20) -> float:
        """
        R² of linear regression of log-price on time.
        High R² → strong trend; low R² → range-bound.
        """
        if len(prices) < window:
            return 0.0
        y = np.log(prices.tail(window).values.astype(float))
        x = np.arange(len(y))
        if np.std(y) == 0:
            return 0.0
        r = np.corrcoef(x, y)[0, 1]
        return float(r ** 2)

    # ── Donchian channel boundaries ───────────────────────────────────────────
    def _channel(self, bars: pd.DataFrame) -> tuple[float, float, float]:
        """Returns (ceiling, floor, mid)."""
        hi    = bars["high"].rolling(self.channel_period).max().iloc[-1]
        lo    = bars["low"].rolling(self.channel_period).min().iloc[-1]
        mid   = (hi + lo) / 2
        return float(hi), float(lo), float(mid)

    # ── Support & Resistance via swing points ─────────────────────────────────
    @staticmethod
    def _swing_levels(bars: pd.DataFrame, lookback: int = 50,
                      n_levels: int = 3) -> tuple[list, list]:
        """
        Identify N strongest support and resistance levels from recent swing highs/lows.
        Returns (support_levels, resistance_levels).
        """
        hi = bars["high"].values[-lookback:]
        lo = bars["low"].values[-lookback:]

        # Swing highs: local maxima
        res = []
        for i in range(2, len(hi) - 2):
            if hi[i] > hi[i-1] and hi[i] > hi[i-2] and hi[i] > hi[i+1] and hi[i] > hi[i+2]:
                res.append(float(hi[i]))

        # Swing lows: local minima
        sup = []
        for i in range(2, len(lo) - 2):
            if lo[i] < lo[i-1] and lo[i] < lo[i-2] and lo[i] < lo[i+1] and lo[i] < lo[i+2]:
                sup.append(float(lo[i]))

        # Return the N most recent levels
        return sorted(sup)[-n_levels:], sorted(res)[:n_levels]

    # ── Main signal ───────────────────────────────────────────────────────────
    def generate_signal(self, symbol: str, bars: pd.DataFrame) -> dict:
        """
        Returns {"signal": "buy"|"sell"|"hold", "score": float, "mode": str, "meta": dict}
        """
        min_bars = self.channel_period + self.atr_period + 5
        if bars is None or len(bars) < min_bars:
            return {"signal": "hold", "score": 0.0, "mode": "insufficient_data", "meta": {}}

        price  = float(bars["close"].iloc[-1])
        atr    = float(self._atr(bars, self.atr_period).iloc[-1])
        r2     = self._r_squared(bars["close"])
        hi, lo, mid = self._channel(bars)
        sup, res = self._swing_levels(bars)

        # Volume confirmation
        vol       = bars["volume"].astype(float)
        vol_ratio = float(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1]) if vol.rolling(20).mean().iloc[-1] > 0 else 1.0

        signal = "hold"
        score  = 0.0
        mode   = "breakout" if r2 >= self.trend_r2_threshold else "mean_reversion"

        if mode == "breakout":
            # ── Breakout mode (Vault #70) ─────────────────────────────────
            # Buy breakout above channel ceiling with volume
            if price >= hi and vol_ratio >= 1.3:
                score  = min(1.0, 0.5 + (price - hi) / atr * 0.2 + (vol_ratio - 1) * 0.1)
                signal = "buy"
            # Short breakdown below channel floor with volume
            elif price <= lo and vol_ratio >= 1.3:
                score  = -min(1.0, 0.5 + (lo - price) / atr * 0.2 + (vol_ratio - 1) * 0.1)
                signal = "sell"
        else:
            # ── Mean-reversion mode (Vault #69 S/R) ─────────────────────
            # Fade moves at channel extremes / support-resistance levels
            channel_width = hi - lo
            if channel_width <= 0:
                pass
            elif price <= lo + atr * 0.5:
                # Near channel floor → potential bounce
                # Confirm with a nearby support level
                near_support = any(abs(price - s) / price < 0.01 for s in sup) if sup else True
                if near_support:
                    score  = 0.6
                    signal = "buy"
            elif price >= hi - atr * 0.5:
                # Near channel ceiling → potential rejection
                near_resist = any(abs(price - r) / price < 0.01 for r in res) if res else True
                if near_resist:
                    score  = -0.6
                    signal = "sell"

        # Stop-loss reference: 1× ATR beyond channel boundary
        stop_long  = lo - atr
        stop_short = hi + atr
        target_long  = mid + (mid - lo) * 1.5   # 1.5× channel half toward ceiling
        target_short = mid - (hi - mid) * 1.5

        return {
            "signal": signal,
            "score":  round(score, 4),
            "mode":   mode,
            "meta": {
                "channel_hi":    round(hi, 4),
                "channel_lo":    round(lo, 4),
                "channel_mid":   round(mid, 4),
                "r_squared":     round(r2, 3),
                "vol_ratio":     round(vol_ratio, 2),
                "atr":           round(atr, 4),
                "stop_long":     round(stop_long, 4),
                "stop_short":    round(stop_short, 4),
                "target_long":   round(target_long, 4),
                "target_short":  round(target_short, 4),
                "support_levels":  [round(s, 2) for s in sup],
                "resist_levels":   [round(r, 2) for r in res],
            }
        }
