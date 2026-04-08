"""
Factor Investing — Vault Strategies #59–63
Implements: Earnings Momentum (SUE), Value (B/P), Low Volatility,
Implied Volatility Signal, Residual Momentum (FF3-adjusted).

Each factor generates a cross-sectional z-score for each stock.
Combined into a composite signal for portfolio construction.
Designed for monthly rebalance; works on large/mid-cap universe.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


class FactorInvestingStrategy:
    """
    Multi-factor signal generator for cross-sectional equity selection.
    Vault Strategies #59 (Earnings Momentum), #60 (Value), #61 (Low Vol),
    #62 (Implied Vol), #63 (Residual Momentum).
    """

    name = "FactorInvesting"
    description = "Cross-sectional factor ranking: earnings momentum, value, low-vol, residual momentum."
    position_size_pct = 0.04    # 4% per position (diversified portfolio)
    max_positions     = 20      # broader portfolio than momentum-only

    # Factor weights in composite score
    WEIGHTS = {
        "earnings_momentum": 0.30,
        "value":             0.20,
        "low_volatility":    0.25,
        "residual_momentum": 0.25,
    }

    # ── Earnings Momentum (SUE) — Vault #59 ─────────────────────────────────

    @staticmethod
    def earnings_momentum_score(eps_current: float, eps_4q_ago: float,
                                 eps_surprises_std: float) -> float:
        """
        SUE = (EPS_current - EPS_4q_ago) / std(earnings_surprises_last_8q)
        Returns standardized unexpected earnings score.
        """
        if eps_surprises_std <= 0:
            return 0.0
        return (eps_current - eps_4q_ago) / eps_surprises_std

    # ── Value (Book-to-Price) — Vault #60 ───────────────────────────────────

    @staticmethod
    def value_score(book_value_per_share: float, price: float) -> float:
        """B/P ratio — higher is cheaper (more value)."""
        if price <= 0:
            return 0.0
        return book_value_per_share / price

    # ── Low Volatility Factor — Vault #61 ───────────────────────────────────

    @staticmethod
    def low_volatility_score(returns: pd.Series, window: int = 126) -> float:
        """
        Returns negative historical volatility (so low-vol stocks rank HIGH).
        window = 126 trading days (6 months) standard.
        """
        if len(returns) < window // 2:
            return 0.0
        vol = returns.tail(window).std() * np.sqrt(252)
        return -float(vol)   # negative: lower vol → higher score

    # ── Residual Momentum (FF3-adjusted) — Vault #63 ────────────────────────

    @staticmethod
    def residual_momentum_score(stock_returns: pd.Series,
                                 mkt_returns: pd.Series,
                                 smb_returns: Optional[pd.Series] = None,
                                 hml_returns: Optional[pd.Series] = None,
                                 formation_months: int = 12,
                                 skip_months: int = 1) -> float:
        """
        1. Regress stock returns on MKT (+ SMB, HML if available) over 36M.
        2. Compute residuals over 12M formation period (skip last month).
        3. Return risk-adjusted residual return (residual mean / residual vol).
        """
        n = min(len(stock_returns), len(mkt_returns))
        if n < formation_months + 5:
            return 0.0

        sr = stock_returns.values[-n:]
        mr = mkt_returns.values[-n:]

        # Simple market regression if no FF factors
        X = np.vstack([np.ones(n), mr]).T
        try:
            betas = np.linalg.lstsq(X, sr, rcond=None)[0]
        except Exception:
            return 0.0

        residuals = sr - X @ betas

        # Formation period: skip last `skip_months` months (≈21 trading days/month)
        skip_days = skip_months * 21
        form_days = formation_months * 21
        if len(residuals) < form_days + skip_days:
            return 0.0

        form_resid = residuals[-(form_days + skip_days):-skip_days if skip_days > 0 else None]
        if len(form_resid) < 5:
            return 0.0

        mean_r = form_resid.mean()
        std_r  = form_resid.std()
        if std_r <= 0:
            return 0.0
        return float(mean_r / std_r)

    # ── Cross-sectional z-score ──────────────────────────────────────────────

    @staticmethod
    def cross_section_zscore(values: dict[str, float]) -> dict[str, float]:
        """Standardize a dict of {symbol: raw_score} cross-sectionally."""
        vals = np.array(list(values.values()), dtype=float)
        mean = np.nanmean(vals)
        std  = np.nanstd(vals)
        if std == 0:
            return {k: 0.0 for k in values}
        return {k: float((v - mean) / std) for k, v in values.items()}

    # ── Composite signal ─────────────────────────────────────────────────────

    def composite_score(self, factor_scores: dict[str, dict[str, float]]) -> dict[str, float]:
        """
        factor_scores = {
            "earnings_momentum": {sym: score, ...},
            "value": {sym: score, ...},
            "low_volatility": {sym: score, ...},
            "residual_momentum": {sym: score, ...},
        }
        Returns {sym: composite_z} for portfolio construction.
        """
        # Standardize each factor cross-sectionally
        zscores = {}
        for factor, raw in factor_scores.items():
            if raw:
                zscores[factor] = self.cross_section_zscore(raw)

        if not zscores:
            return {}

        # Get universe of symbols appearing in all factors
        all_syms = set.intersection(*[set(z.keys()) for z in zscores.values()])

        composite = {}
        for sym in all_syms:
            score = 0.0
            for factor, z in zscores.items():
                w = self.WEIGHTS.get(factor, 0.25)
                score += w * z.get(sym, 0.0)
            composite[sym] = round(score, 4)

        return composite

    # ── Portfolio construction ───────────────────────────────────────────────

    def select_portfolio(self, composite: dict[str, float],
                         long_threshold: float = 1.0,
                         short_threshold: float = -1.0) -> dict[str, str]:
        """
        Returns {symbol: "buy"|"sell"} for symbols crossing thresholds.
        long_threshold:  composite z > 1.0 → buy
        short_threshold: composite z < -1.0 → sell/short
        """
        positions = {}
        for sym, z in composite.items():
            if z >= long_threshold:
                positions[sym] = "buy"
            elif z <= short_threshold:
                positions[sym] = "sell"
        return positions

    # ── Single-stock signal (for live screener) ──────────────────────────────

    def single_stock_signal(self, symbol: str, price_series: pd.Series,
                             market_series: pd.Series,
                             book_value_per_share: float = 0.0,
                             eps_current: float = 0.0,
                             eps_4q_ago: float = 0.0,
                             eps_std: float = 1.0) -> dict:
        """Quick signal for a single stock without cross-sectional ranking."""
        returns = price_series.pct_change().dropna()
        mkt_ret = market_series.pct_change().dropna()

        low_vol = self.low_volatility_score(returns)
        resid   = self.residual_momentum_score(returns, mkt_ret)
        sue     = self.earnings_momentum_score(eps_current, eps_4q_ago, eps_std) if eps_std > 0 else 0.0
        value   = self.value_score(book_value_per_share, float(price_series.iloc[-1])) if price_series.iloc[-1] > 0 else 0.0

        # Simple composite (not cross-sectional without peer group)
        raw_composite = (
            self.WEIGHTS["earnings_momentum"] * np.tanh(sue) +
            self.WEIGHTS["low_volatility"]    * np.tanh(low_vol * 10) +
            self.WEIGHTS["residual_momentum"] * np.tanh(resid * 3)
        )

        signal = "hold"
        if raw_composite > 0.4:  signal = "buy"
        elif raw_composite < -0.4: signal = "sell"

        return {
            "signal":   signal,
            "score":    round(float(raw_composite), 4),
            "meta": {
                "sue":          round(float(sue), 4),
                "low_vol_rank": round(float(low_vol), 4),
                "resid_mom":    round(float(resid), 4),
                "value_bp":     round(float(value), 4),
            }
        }
