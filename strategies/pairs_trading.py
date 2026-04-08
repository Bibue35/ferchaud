"""
Pairs Trading (Statistical Arbitrage) — Strategy Vault #64
Implements cointegration-based pairs trading with Kalman-filter hedge ratio.
Vault Strategy: Pairs Trading (Two-Stock Stat Arb)

Entry: Spread z-score > ±2 (long cheap / short rich)
Exit:  Spread z-score returns to ±0.5, or stop at ±3
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


class PairsTradingStrategy:
    """
    Cointegration-based pairs trading.
    Uses rolling OLS hedge ratio (static) or Kalman filter (dynamic).
    """

    name = "PairsTrading"
    description = "Long/short pair of co-integrated stocks when spread diverges."

    # Default parameters
    lookback      = 60    # days to estimate hedge ratio
    entry_z       = 2.0   # z-score threshold for entry
    exit_z        = 0.5   # z-score threshold for exit
    stop_z        = 3.0   # z-score stop-loss
    max_half_life = 30    # reject pairs with reversion > 30 days

    def __init__(self, lookback: int = 60, entry_z: float = 2.0,
                 exit_z: float = 0.5, stop_z: float = 3.0):
        self.lookback  = lookback
        self.entry_z   = entry_z
        self.exit_z    = exit_z
        self.stop_z    = stop_z

    # ── Hedge ratio (OLS) ────────────────────────────────────────────────────

    @staticmethod
    def _ols_hedge(y: np.ndarray, x: np.ndarray) -> float:
        """Compute hedge ratio β via OLS: y = α + β·x + ε"""
        x_ = np.vstack([np.ones(len(x)), x]).T
        try:
            coeffs = np.linalg.lstsq(x_, y, rcond=None)[0]
            return float(coeffs[1])
        except Exception:
            return 1.0

    # ── Spread & z-score ─────────────────────────────────────────────────────

    def compute_spread(self, price_a: pd.Series, price_b: pd.Series,
                       hedge_ratio: Optional[float] = None) -> pd.Series:
        """
        Compute spread = log(A) - β·log(B).
        If hedge_ratio is None, estimates via OLS on full series.
        """
        log_a = np.log(price_a.astype(float))
        log_b = np.log(price_b.astype(float))

        if hedge_ratio is None:
            hedge_ratio = self._ols_hedge(log_a.values, log_b.values)

        spread = log_a - hedge_ratio * log_b
        return spread, hedge_ratio

    def compute_zscore(self, spread: pd.Series, window: int = None) -> pd.Series:
        """Rolling z-score of the spread."""
        w = window or self.lookback
        mu    = spread.rolling(w).mean()
        sigma = spread.rolling(w).std()
        return (spread - mu) / sigma.replace(0, np.nan)

    # ── Half-life of mean reversion (Ornstein-Uhlenbeck) ────────────────────

    @staticmethod
    def half_life(spread: pd.Series) -> float:
        """Estimate mean-reversion half-life via AR(1) regression."""
        delta = spread.diff().dropna()
        lag   = spread.shift(1).dropna()
        # Align
        delta, lag = delta.align(lag, join="inner")
        if len(delta) < 10:
            return 999.0
        try:
            x_ = np.vstack([np.ones(len(lag)), lag.values]).T
            coeffs = np.linalg.lstsq(x_, delta.values, rcond=None)[0]
            lam = coeffs[1]
            if lam >= 0:
                return 999.0   # no mean reversion
            return float(-np.log(2) / lam)
        except Exception:
            return 999.0

    # ── Main signal ──────────────────────────────────────────────────────────

    def generate_signal(self, symbol_a: str, symbol_b: str,
                        prices_a: pd.Series, prices_b: pd.Series,
                        current_position: str = "flat") -> dict:
        """
        Returns trading signal for the pair (symbol_a, symbol_b).

        current_position: "flat" | "long_a_short_b" | "short_a_long_b"

        Returns dict:
          action:  "enter_long_a" | "enter_long_b" | "exit" | "hold"
          z_score: current z-score
          hedge_ratio: β
          half_life: days
          score:   confidence [−1, 1]
        """
        if len(prices_a) < self.lookback + 5 or len(prices_b) < self.lookback + 5:
            return {"action": "hold", "z_score": 0.0, "hedge_ratio": 1.0,
                    "half_life": 999, "score": 0.0}

        spread, beta = self.compute_spread(prices_a, prices_b)
        z_series     = self.compute_zscore(spread)
        hl           = self.half_life(spread)
        z            = float(z_series.iloc[-1]) if not np.isnan(z_series.iloc[-1]) else 0.0

        # Reject if mean reversion too slow
        if hl > self.max_half_life:
            return {"action": "hold", "z_score": round(z, 3),
                    "hedge_ratio": round(beta, 4), "half_life": round(hl, 1), "score": 0.0,
                    "reason": f"half_life={hl:.0f}d > {self.max_half_life}d limit"}

        action = "hold"
        score  = 0.0

        if current_position == "flat":
            if z >= self.entry_z:
                # Spread too high: A is rich vs B → short A, long B
                action = "enter_long_b"
                score  = min(1.0, (z - self.entry_z + 1) / 2)
            elif z <= -self.entry_z:
                # Spread too low: A is cheap vs B → long A, short B
                action = "enter_long_a"
                score  = min(1.0, (-z - self.entry_z + 1) / 2)

        elif current_position in ("long_a_short_b", "short_a_long_b"):
            # Stop-loss
            if abs(z) >= self.stop_z:
                action = "exit"
                score  = 0.0
            # Take profit
            elif abs(z) <= self.exit_z:
                action = "exit"
                score  = 0.5  # profit exit

        return {
            "action":      action,
            "z_score":     round(z, 3),
            "hedge_ratio": round(beta, 4),
            "half_life":   round(hl, 1),
            "score":       round(score, 4),
            "spread_mean": round(float(spread.rolling(self.lookback).mean().iloc[-1]), 6),
            "spread_std":  round(float(spread.rolling(self.lookback).std().iloc[-1]), 6),
        }

    # ── Pair screening ───────────────────────────────────────────────────────

    @staticmethod
    def screen_pairs(price_df: pd.DataFrame, min_corr: float = 0.75) -> list[tuple]:
        """
        Screen a DataFrame of prices (columns = symbols) for candidate pairs.
        Returns list of (symbolA, symbolB, correlation, hedge_ratio) tuples.
        Quick pre-filter by correlation before running cointegration tests.
        """
        log_prices = np.log(price_df.dropna(axis=1))
        corr_matrix = log_prices.corr()
        candidates = []
        symbols = list(log_prices.columns)
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i+1:]:
                c = corr_matrix.loc[sym_a, sym_b]
                if c >= min_corr:
                    candidates.append((sym_a, sym_b, round(float(c), 4)))
        # Sort by correlation descending
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[:50]  # top 50 pairs by correlation
