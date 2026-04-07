"""
Risk Management Engine v2
━━━━━━━━━━━━━━━━━━━━━━━━
Production-grade risk with:
  • Bayesian Adaptive Kelly Criterion (posterior-shrunk position sizing)
  • Value-at-Risk (historical + parametric) and Conditional VaR (Expected Shortfall)
  • Volatility targeting (EWMA vol forecast → dynamic leverage)
  • Risk parity across strategies (equal risk contribution allocation)
  • ATR-based stop-loss / take-profit
  • Max-drawdown circuit breaker
  • Cross-strategy correlation monitoring
"""
import math
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

from config import CONFIG
from utils.logger import get_logger

log = get_logger("core.risk")


# ═══════════════════════════════════════════════════════════════════════════════
#  Bayesian Kelly Criterion
# ═══════════════════════════════════════════════════════════════════════════════

class BayesianKelly:
    """
    Adaptive Kelly with Normal-Inverse-Gamma conjugate prior.

    The posterior naturally shrinks the Kelly fraction toward zero when:
      - Few observations (n small)
      - High return variance
      - Prior is sceptical (mu_0 ≈ 0)

    Returns fractional Kelly (scaled by CONFIG.kelly_fraction) as shares.
    """

    def __init__(
        self,
        mu_0: float = 0.0,       # Prior mean return (sceptical = 0)
        kappa_0: float = 5.0,    # Prior strength on mean
        alpha_0: float = 3.0,    # Prior shape for variance (>2 for finite var)
        beta_0: float = 0.001,   # Prior scale for variance
    ) -> None:
        self.mu_0 = mu_0
        self.kappa_0 = kappa_0
        self.alpha_0 = alpha_0
        self.beta_0 = beta_0
        self._returns: deque = deque(maxlen=500)

    def add_return(self, r: float) -> None:
        self._returns.append(r)

    def optimal_fraction(self) -> float:
        """Return the Bayesian Kelly fraction ∈ [-1, 1] (already scaled by kelly_fraction)."""
        n = len(self._returns)
        if n < 10:
            return 0.0

        rets = np.array(self._returns)
        x_bar = rets.mean()
        s2 = rets.var(ddof=1)

        kappa_n = self.kappa_0 + n
        mu_n = (self.kappa_0 * self.mu_0 + n * x_bar) / kappa_n
        alpha_n = self.alpha_0 + n / 2
        beta_n = (self.beta_0 + 0.5 * n * s2 +
                  0.5 * self.kappa_0 * n * (x_bar - self.mu_0) ** 2 / kappa_n)

        # Posterior variance estimate
        sigma2_n = beta_n / max(alpha_n - 1, 0.5)

        # Kelly = mu / sigma^2, but shrunk by posterior uncertainty
        raw_kelly = mu_n / sigma2_n if sigma2_n > 1e-12 else 0.0
        scaled = raw_kelly * CONFIG.kelly_fraction
        return float(np.clip(scaled, -1.0, 1.0))

    def size_in_shares(
        self, portfolio_value: float, price: float, regime_scale: float = 1.0
    ) -> float:
        f = self.optimal_fraction() * regime_scale
        dollars = portfolio_value * abs(f)
        dollars = min(dollars, portfolio_value * CONFIG.max_position_size)
        return math.floor(dollars / price) if price > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  VaR / CVaR
# ═══════════════════════════════════════════════════════════════════════════════

class VaREngine:
    """Historical and parametric Value-at-Risk + Expected Shortfall (CVaR)."""

    @staticmethod
    def historical_var(returns: pd.Series, confidence: float = 0.99) -> float:
        """1-day VaR at given confidence level (as positive loss)."""
        if len(returns) < 30:
            return 0.0
        return -float(np.percentile(returns.dropna(), (1 - confidence) * 100))

    @staticmethod
    def parametric_var(returns: pd.Series, confidence: float = 0.99) -> float:
        mu = returns.mean()
        sigma = returns.std()
        z = norm.ppf(1 - confidence)
        return -(mu + z * sigma)

    @staticmethod
    def cvar(returns: pd.Series, confidence: float = 0.99) -> float:
        """Expected Shortfall — average loss beyond VaR (always worse than VaR)."""
        if len(returns) < 30:
            return 0.0
        cutoff = np.percentile(returns.dropna(), (1 - confidence) * 100)
        tail = returns[returns <= cutoff]
        return -float(tail.mean()) if len(tail) > 0 else 0.0

    @staticmethod
    def portfolio_var(
        returns_matrix: pd.DataFrame, weights: np.ndarray, confidence: float = 0.99
    ) -> float:
        """Portfolio VaR using covariance method."""
        cov = returns_matrix.cov()
        port_vol = float(np.sqrt(weights @ cov.values @ weights))
        z = norm.ppf(confidence)
        return port_vol * z


# ═══════════════════════════════════════════════════════════════════════════════
#  Volatility Targeting
# ═══════════════════════════════════════════════════════════════════════════════

class VolatilityTargeter:
    """
    Scale portfolio leverage so that realised vol tracks a target.

    Uses EWMA volatility forecast. Capped by [min_leverage, max_leverage].
    """

    def __init__(self, halflife: int = 20) -> None:
        self._lam = np.exp(-np.log(2) / halflife)
        self._var_ewma: Optional[float] = None

    def update(self, daily_return: float) -> None:
        if self._var_ewma is None:
            self._var_ewma = daily_return ** 2
        else:
            self._var_ewma = self._lam * self._var_ewma + (1 - self._lam) * daily_return ** 2

    @property
    def forecast_vol(self) -> float:
        """Annualised vol forecast."""
        if self._var_ewma is None or self._var_ewma <= 0:
            return CONFIG.vol_target  # Assume target if no data
        return float(np.sqrt(self._var_ewma * 252))

    @property
    def leverage(self) -> float:
        fvol = self.forecast_vol
        if fvol <= 0:
            return 1.0
        raw = CONFIG.vol_target / fvol
        return float(np.clip(raw, CONFIG.min_leverage, CONFIG.max_leverage))


# ═══════════════════════════════════════════════════════════════════════════════
#  Risk Parity (Equal Risk Contribution)
# ═══════════════════════════════════════════════════════════════════════════════

def risk_parity_weights(cov_matrix: np.ndarray) -> np.ndarray:
    """
    Compute Equal Risk Contribution (ERC) weights.

    Each strategy contributes equally to total portfolio variance.
    Falls back to inverse-volatility if optimisation fails.
    """
    n = cov_matrix.shape[0]
    if n == 1:
        return np.array([1.0])

    # Inverse-vol as initial guess and fallback
    vols = np.sqrt(np.diag(cov_matrix))
    vols = np.maximum(vols, 1e-8)
    inv_vol = (1.0 / vols)
    inv_vol /= inv_vol.sum()

    def objective(w):
        w = np.abs(w)
        port_var = w @ cov_matrix @ w
        if port_var <= 0:
            return 1e12
        port_vol = np.sqrt(port_var)
        marginal = cov_matrix @ w
        rc = w * marginal / port_vol
        target_rc = port_vol / n
        return float(np.sum((rc - target_rc) ** 2))

    try:
        result = minimize(
            objective,
            x0=inv_vol,
            method="SLSQP",
            bounds=[(0.01, 1.0)] * n,
            constraints={"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            options={"maxiter": 500, "ftol": 1e-12},
        )
        if result.success:
            w = np.abs(result.x)
            return w / w.sum()
    except Exception:
        pass

    return inv_vol


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Risk Engine
# ═══════════════════════════════════════════════════════════════════════════════

class RiskEngine:
    def __init__(self, portfolio_value: float) -> None:
        self._peak_value = portfolio_value
        self._halted = False

        # Sub-engines
        self.bayesian_kelly = BayesianKelly()
        self.var_engine = VaREngine()
        self.vol_targeter = VolatilityTargeter(halflife=CONFIG.vol_lookback // 3)

        # Strategy return tracking for risk-parity
        self._strategy_returns: Dict[str, deque] = {}

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def update_portfolio_value(self, value: float) -> None:
        if value > self._peak_value:
            self._peak_value = value
        drawdown = (self._peak_value - value) / self._peak_value
        if drawdown >= CONFIG.max_drawdown:
            if not self._halted:
                log.critical(
                    "MAX DRAWDOWN BREACHED: %.1f%% (limit %.1f%%). Trading HALTED.",
                    drawdown * 100, CONFIG.max_drawdown * 100,
                )
            self._halted = True
        else:
            self._halted = False

        # Feed daily return to vol targeter
        if self._peak_value > 0:
            daily_ret = (value - self._peak_value) / self._peak_value
            self.vol_targeter.update(daily_ret)

    @property
    def is_halted(self) -> bool:
        return self._halted

    # ── Position sizing ───────────────────────────────────────────────────────

    def kelly_size(
        self,
        portfolio_value: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        price: float,
    ) -> float:
        """Classic Kelly (backward compat). Prefer bayesian_kelly for new code."""
        if avg_loss <= 0 or price <= 0:
            return 0.0
        R = avg_win / avg_loss
        kelly = (win_rate * R - (1 - win_rate)) / R
        kelly = max(0.0, kelly) * CONFIG.kelly_fraction
        max_dollars = portfolio_value * CONFIG.max_position_size
        kelly_dollars = portfolio_value * kelly
        dollars = min(kelly_dollars, max_dollars)
        return float(math.floor(dollars / price))

    def risk_based_size(
        self,
        portfolio_value: float,
        price: float,
        stop_distance: float,
        regime_scale: float = 1.0,
        confidence: float = 0.5,
    ) -> float:
        """Size position proportional to confidence (0–1).

        confidence=0.5  → base Kelly/risk sizing (default)
        confidence=0.75 → 1.5× base size
        confidence=0.9  → 2.5× base size (approaching full position)
        confidence=1.0  → ALL IN: deploy all available cash into this trade
        """
        if stop_distance <= 0 or price <= 0:
            return 0.0

        # All-in: confidence == 1.0 → use 95% of available equity
        if confidence >= 1.0:
            all_in_shares = math.floor((portfolio_value * 0.95) / price)
            return float(all_in_shares)

        risk_dollars = portfolio_value * CONFIG.max_portfolio_risk
        vol_leverage = self.vol_targeter.leverage
        risk_dollars *= regime_scale * vol_leverage

        # Scale by confidence: 0.5 = 1×, 0.75 = 1.5×, 0.9 = 2.5×
        conf_mult = 1.0 + (confidence / (1.0 - confidence + 1e-9)) * 0.5
        conf_mult = min(conf_mult, 10.0)
        risk_dollars *= conf_mult

        shares = math.floor(risk_dollars / stop_distance)

        # Position cap scales with confidence:
        # conf 0.5 → max_position_size (25%), conf 0.7 → 35%, conf 0.9 → 50%
        effective_cap = CONFIG.max_position_size + confidence * (0.55 - CONFIG.max_position_size)
        effective_cap = min(effective_cap, 0.55)  # Hard cap at 55% per position
        max_shares = math.floor((portfolio_value * effective_cap) / price)
        return float(min(shares, max_shares))

    # ── Stop / target levels ──────────────────────────────────────────────────

    def atr_stops(
        self,
        entry_price: float,
        atr_value: float,
        side: str,
        atr_stop_mult: float = 2.0,
        atr_target_mult: float = 3.0,
    ) -> Tuple[float, float]:
        if side == "buy":
            stop = entry_price - atr_stop_mult * atr_value
            target = entry_price + atr_target_mult * atr_value
        else:
            stop = entry_price + atr_stop_mult * atr_value
            target = entry_price - atr_target_mult * atr_value
        return round(stop, 4), round(target, 4)

    # ── Pre-trade checks ──────────────────────────────────────────────────────

    def pre_trade_check(
        self,
        symbol: str,
        qty: float,
        price: float,
        portfolio_value: float,
        existing_positions: dict,
        confidence: float = 0.5,
    ) -> Tuple[bool, str]:
        if self._halted:
            return False, "Trading halted — max drawdown exceeded"
        if qty <= 0:
            return False, f"Invalid quantity: {qty}"

        trade_value = qty * price
        position_pct = trade_value / portfolio_value

        # High confidence trades get larger position allowances
        # confidence=1.0 → no position cap (all-in allowed)
        # confidence=0.9 → 50% of portfolio allowed per position
        # confidence=0.5 → normal CONFIG.max_position_size cap
        if confidence < 1.0:
            # Must match risk_based_size cap + 5% buffer for rounding
            effective_max = CONFIG.max_position_size + confidence * (0.55 - CONFIG.max_position_size)
            effective_max = min(effective_max, 0.55) + 0.05  # 5% buffer
            if position_pct > effective_max:
                return False, (
                    f"Position size {position_pct:.1%} exceeds limit "
                    f"{effective_max:.1%} (confidence={confidence:.2f})"
                )

        # ── Virtual $5K cap enforcement ────────────────────────────────────────
        from config import CONFIG
        VIRTUAL_CAP = getattr(CONFIG, 'virtual_cap', 5000.0)
        all_exposure = sum(abs(p.get("market_value", 0)) for p in existing_positions.values())
        if (all_exposure + trade_value) > VIRTUAL_CAP * 1.05:  # 5% tolerance
            return False, f"Virtual cap ${VIRTUAL_CAP:.0f} reached (deployed ${all_exposure:.0f}) — skipping"

        return True, "OK"

    # ── Strategy-level risk tracking ──────────────────────────────────────────

    def record_strategy_return(self, strategy_name: str, daily_return: float) -> None:
        if strategy_name not in self._strategy_returns:
            self._strategy_returns[strategy_name] = deque(maxlen=252)
        self._strategy_returns[strategy_name].append(daily_return)

    def compute_strategy_weights(self, strategy_names: List[str]) -> Dict[str, float]:
        """
        Compute risk-parity weights across strategies.

        Falls back to equal weight if insufficient data.
        """
        n = len(strategy_names)
        if n == 0:
            return {}
        equal = {s: 1.0 / n for s in strategy_names}

        # Check for fixed weights in config
        if CONFIG.has_fixed_weights:
            weight_map = {
                "mean_reversion": CONFIG.weight_mean_reversion,
                "momentum": CONFIG.weight_momentum,
                "stat_arb": CONFIG.weight_stat_arb,
                "multi_factor": CONFIG.weight_multi_factor,
            }
            weights = {}
            total = 0.0
            for name in strategy_names:
                w = weight_map.get(name)
                if w is not None:
                    weights[name] = w
                    total += w
                else:
                    weights[name] = 0.0
            if total > 0:
                return {k: v / total for k, v in weights.items()}
            return equal

        # Need >= 60 days of returns for each strategy
        available = [s for s in strategy_names
                     if s in self._strategy_returns and len(self._strategy_returns[s]) >= 60]
        if len(available) < n:
            return equal

        # Build return matrix
        min_len = min(len(self._strategy_returns[s]) for s in available)
        matrix = np.column_stack([
            np.array(list(self._strategy_returns[s]))[-min_len:]
            for s in available
        ])
        cov = np.cov(matrix, rowvar=False)

        # Check for high correlation
        corr = np.corrcoef(matrix, rowvar=False)
        for i in range(n):
            for j in range(i + 1, n):
                if abs(corr[i, j]) > CONFIG.max_correlation:
                    log.warning(
                        "High correlation %.2f between %s and %s",
                        corr[i, j], available[i], available[j],
                    )

        weights_arr = risk_parity_weights(cov)
        return {available[i]: float(weights_arr[i]) for i in range(n)}

    # ── Historical win-rate estimation ────────────────────────────────────────

    @staticmethod
    def estimate_win_rate(returns: pd.Series) -> Tuple[float, float, float]:
        wins = returns[returns > 0]
        losses = returns[returns <= 0]
        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.5
        avg_win = wins.mean() if not wins.empty else 0.01
        avg_loss = abs(losses.mean()) if not losses.empty else 0.01
        return win_rate, avg_win, avg_loss
