"""
Alpha Combination Engine — Institutional 11-Step Signal Combination
Based on: "The Math Behind Combining 50 Weak Signals Into One Winning Trade" (@RohOnChain)

Fundamental Law of Active Management:
    IR = IC × √N
    IR  = Information Ratio of combined system
    IC  = average information coefficient per signal
    N   = number of genuinely independent signals

The 11-step procedure converts N weak, independent signals into a single
optimal mega-alpha with mathematically justified position sizing.
"""

import math
import json
import os
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────

def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0

def _variance(xs: list) -> float:
    if len(xs) < 2:
        return 1e-9
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)

def _std(xs: list) -> float:
    return math.sqrt(_variance(xs))

def _covariance(xs: list, ys: list) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = _mean(xs[:n]), _mean(ys[:n])
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n

def _ols_beta_no_intercept(x: list, y: list) -> float:
    """OLS beta without intercept: β = Σ(xi·yi) / Σ(xi²)"""
    n = min(len(x), len(y))
    num = sum(x[i] * y[i] for i in range(n))
    den = sum(x[i] ** 2 for i in range(n))
    return num / den if den != 0 else 0.0

def _information_coefficient(predictions: list, actuals: list) -> float:
    """Pearson correlation between predicted and actual returns."""
    n = min(len(predictions), len(actuals))
    if n < 4:
        return 0.0
    std_p = _std(predictions[:n])
    std_a = _std(actuals[:n])
    if std_p < 1e-9 or std_a < 1e-9:
        return 0.0
    cov = _covariance(predictions[:n], actuals[:n])
    return cov / (std_p * std_a)



# ─────────────────────────────────────────────
# Step Engine — 11-step institutional procedure
# ─────────────────────────────────────────────

class AlphaCombinationEngine:
    """
    Converts N signal return histories into optimal combination weights.
    Implements the exact 11-step procedure from institutional research.
    """

    def __init__(self, lookback_d: int = 20):
        self.lookback_d = lookback_d  # d for moving average expected return

    def compute_weights(self, signal_returns: Dict[str, List[float]]) -> Dict[str, float]:
        """
        signal_returns: {signal_name: [realized_return_t1, t2, ...t_M]}
        Returns: {signal_name: optimal_weight}
        """
        names = [k for k, v in signal_returns.items() if len(v) >= max(10, self.lookback_d + 2)]
        if len(names) < 2:
            # Not enough data — equal weight
            n = len(signal_returns)
            return {k: 1.0/n for k in signal_returns} if n > 0 else {}

        M = min(len(signal_returns[n]) for n in names)
        R = {n: signal_returns[n][-M:] for n in names}  # align lengths

        # ── Step 1: Raw return series R(i,s) ──────────────────────────────
        # Already in R dict

        # ── Step 2: Remove drift — serially demeaned returns ──────────────
        X = {}
        for name in names:
            mu = _mean(R[name])
            X[name] = [r - mu for r in R[name]]

        # ── Step 3: Sample variance per signal ────────────────────────────
        sigma2 = {n: _variance(X[n]) for n in names}
        sigma  = {n: math.sqrt(max(sigma2[n], 1e-12)) for n in names}

        # ── Step 4: Normalize by std dev → Y(i,s) ────────────────────────
        Y = {n: [x / sigma[n] for x in X[n]] for n in names}

        # ── Step 5: Retain first M periods (drop most recent) ─────────────
        Y5 = {n: Y[n][:-1] for n in names}
        M5 = M - 1

        # ── Step 6: Cross-sectional demean at each time step ──────────────
        Lambda = {}
        for n in names:
            Lambda[n] = []
        for s in range(M5):
            cross_mean = _mean([Y5[n][s] for n in names])
            for n in names:
                Lambda[n].append(Y5[n][s] - cross_mean)

        # ── Step 7: Retain M-1 periods ────────────────────────────────────
        M7 = M5 - 1
        Lambda7 = {n: Lambda[n][:M7] for n in names}

        # ── Step 8: Expected forward return (d-day MA), normalized ─────────
        d = min(self.lookback_d, len(R[names[0]]))
        E = {}
        for n in names:
            recent = R[n][-d:]
            E_raw = _mean(recent)
            E[n] = E_raw / sigma[n]  # normalize to same units

        # ── Step 9: Residual of E after removing shared variance ──────────
        # Regression of E_normalized(i) on Lambda(i,s), no intercept
        # residual ε(i) = E(i) − β(i) × mean(Lambda(i,:))
        epsilon = {}
        for n in names:
            if M7 < 2:
                epsilon[n] = E[n]
                continue
            lam_mean = _mean(Lambda7[n])
            # OLS: E(i) ~ β * lam_mean  (scalar regression, 1 obs)
            # Use Lambda series to compute beta
            lam_series = Lambda7[n]
            e_repeated = [E[n]] * M7  # project E onto Lambda series
            beta = _ols_beta_no_intercept(lam_series, e_repeated)
            fitted = beta * lam_mean
            epsilon[n] = E[n] - fitted  # independent component of expected return

        # ── Step 10: Signal weight ─────────────────────────────────────────
        # w(i) = η × ε(i) / σ(i)
        raw_w = {n: epsilon[n] / sigma[n] for n in names}

        # ── Step 11: Normalize so Σ|w(i)| = 1 ─────────────────────────────
        total_abs = sum(abs(w) for w in raw_w.values())
        if total_abs < 1e-9:
            n_signals = len(names)
            return {n: 1.0 / n_signals for n in names}

        weights = {n: raw_w[n] / total_abs for n in names}
        # Include any signals that didn't have enough data at zero weight
        for k in signal_returns:
            if k not in weights:
                weights[k] = 0.0
        return weights



# ─────────────────────────────────────────────
# Kelly Sizer
# ─────────────────────────────────────────────

class EmpiricalKelly:
    """
    Empirical Kelly criterion adjusted for estimation uncertainty.
    f_empirical = f_kelly × (1 − CV_edge)
    CV_edge = coefficient of variation of edge estimates across Monte Carlo paths.
    """

    def __init__(self, n_simulations: int = 1000, max_fraction: float = 0.25):
        self.n_simulations = n_simulations
        self.max_fraction   = max_fraction  # never bet more than 25% on any signal

    def _kelly_fraction(self, p_win: float, b_ratio: float) -> float:
        """Standard Kelly: f = (p*b - q) / b"""
        if b_ratio <= 0 or p_win <= 0:
            return 0.0
        q = 1.0 - p_win
        return (p_win * b_ratio - q) / b_ratio

    def _cv_edge(self, return_history: list) -> float:
        """
        Coefficient of variation of edge across Monte Carlo re-samples.
        Higher CV → less certain → shrink Kelly more.
        """
        n = len(return_history)
        if n < 10:
            return 0.5  # high uncertainty when little data
        # Bootstrap: resample return_history 1000 times, compute mean each time
        import random
        means = []
        for _ in range(self.n_simulations):
            sample = [random.choice(return_history) for _ in range(n)]
            means.append(_mean(sample))
        mu  = _mean(means)
        std = _std(means)
        return (std / abs(mu)) if abs(mu) > 1e-9 else 1.0

    def size(self, p_win: float, b_ratio: float, return_history: list) -> float:
        """
        Returns fraction of portfolio to allocate.
        p_win: estimated win probability (0-1)
        b_ratio: profit/loss ratio (e.g., 2.0 = win $2 per $1 risked)
        return_history: list of past realized returns for this signal
        """
        f_kelly = self._kelly_fraction(p_win, b_ratio)
        if f_kelly <= 0:
            return 0.0
        cv = self._cv_edge(return_history) if return_history else 0.5
        f_empirical = f_kelly * (1.0 - min(cv, 0.95))
        return max(0.0, min(f_empirical, self.max_fraction))



# ─────────────────────────────────────────────
# Main AlphaCombiner — integrates with existing strategies
# ─────────────────────────────────────────────

HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".alpha_combiner_history.json")

class AlphaCombiner:
    """
    Wraps all existing bot strategies and combines their signals using the
    11-step institutional alpha combination engine.

    Usage in main.py:
        combiner = AlphaCombiner(strategies)
        combined = combiner.run(symbol)
        # combined = {"signal": "buy"|"sell"|"hold", "score": float,
        #             "kelly_fraction": float, "n_independent": float,
        #             "ir_estimate": float, "weights": {...}}
    """

    # Map strategy name → signal type label
    SIGNAL_LABELS = {
        "MeanReversion":       "mean_reversion",
        "Momentum":            "momentum",
        "StatArb":             "stat_arb",
        "MarketMaking":        "market_making",
        "MultiFactorML":       "multi_factor",
        "Scalper":             "scalper",
        "Catalyst":            "catalyst",
        "OptionsCatalyst":     "options_catalyst",
        "GapShort":            "gap_short",
        "PredictiveShort":     "predictive_short",
        "AggressiveBreakout":  "aggressive_breakout",
    }

    def __init__(self, strategies=None, lookback_d: int = 20,
                 min_history: int = 15, reweight_every: int = 50):
        self.strategies      = strategies or []
        self.engine          = AlphaCombinationEngine(lookback_d=lookback_d)
        self.kelly           = EmpiricalKelly()
        self.min_history     = min_history
        self.reweight_every  = reweight_every

        # {signal_name: [realized_return, ...]}
        self.return_history: Dict[str, List[float]] = {}
        # {signal_name: last_predicted_score}
        self._pending: Dict[str, float] = {}
        # current optimal weights
        self.weights: Dict[str, float] = {}
        self._iteration = 0

        self._load_history()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load_history(self):
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE) as f:
                    data = json.load(f)
                self.return_history = data.get("returns", {})
                self.weights        = data.get("weights", {})
                self._iteration     = data.get("iteration", 0)
        except Exception:
            pass

    def _save_history(self):
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump({
                    "returns":   self.return_history,
                    "weights":   self.weights,
                    "iteration": self._iteration,
                }, f)
        except Exception:
            pass


    # ── Signal recording ───────────────────────────────────────────────────

    def record_prediction(self, signal_name: str, predicted_score: float):
        """Call this when a strategy emits a signal (before outcome known)."""
        self._pending[signal_name] = predicted_score

    def record_outcome(self, signal_name: str, realized_return: float):
        """
        Call this after the trade resolves with the actual P&L.
        realized_return: pct return (e.g. 0.02 = +2%)
        """
        if signal_name not in self.return_history:
            self.return_history[signal_name] = []
        self.return_history[signal_name].append(realized_return)
        # Cap history to 500 observations to prevent unbounded memory
        if len(self.return_history[signal_name]) > 500:
            self.return_history[signal_name] = self.return_history[signal_name][-500:]

    # ── Information Coefficient measurement ───────────────────────────────

    def measure_ic(self, signal_name: str) -> float:
        """IC = correlation(predictions, actuals) for a given signal."""
        hist = self.return_history.get(signal_name, [])
        pend = list(self._pending.values())
        if len(hist) < 10:
            return 0.0
        n = min(len(hist), len(pend))
        if n < 4:
            return 0.0
        return _information_coefficient(pend[-n:], hist[-n:])

    def effective_n(self) -> float:
        """
        Estimate number of genuinely independent signals via correlation matrix.
        N_eff ≈ N / avg_pairwise_correlation_factor
        """
        names = list(self.return_history.keys())
        n = len(names)
        if n < 2:
            return float(n)
        # Build pairwise correlation matrix
        total_corr = 0.0
        pairs = 0
        for i in range(n):
            for j in range(i + 1, n):
                h1 = self.return_history[names[i]]
                h2 = self.return_history[names[j]]
                k  = min(len(h1), len(h2))
                if k < 4:
                    continue
                cov = _covariance(h1[-k:], h2[-k:])
                s1  = _std(h1[-k:])
                s2  = _std(h2[-k:])
                if s1 > 1e-9 and s2 > 1e-9:
                    total_corr += abs(cov / (s1 * s2))
                    pairs += 1
        if pairs == 0:
            return float(n)
        avg_abs_corr = total_corr / pairs
        # N_eff formula: N / (1 + (N-1) × avg_corr)
        n_eff = n / (1.0 + (n - 1) * avg_abs_corr)
        return max(1.0, n_eff)

    def information_ratio(self) -> float:
        """IR = IC × √N_eff"""
        names = [n for n in self.return_history if len(self.return_history[n]) >= self.min_history]
        if not names:
            return 0.0
        ics = []
        for name in names:
            hist = self.return_history[name]
            pred = self._pending.get(name, 0.0)
            if len(hist) >= 4:
                ic = _information_coefficient([pred] * len(hist), hist)
                ics.append(ic)
        if not ics:
            return 0.0
        avg_ic  = _mean(ics)
        n_eff   = self.effective_n()
        return avg_ic * math.sqrt(n_eff)


    # ── Core combination ────────────────────────────────────────────────────

    def combine(self, current_signals: Dict[str, float]) -> dict:
        """
        current_signals: {signal_name: score}  (score: positive=bullish, negative=bearish)
        Returns combined signal dict.
        """
        self._iteration += 1

        # Recompute weights every N iterations (expensive operation)
        if self._iteration % self.reweight_every == 1 or not self.weights:
            eligible = {k: v for k, v in self.return_history.items()
                        if len(v) >= self.min_history}
            if eligible:
                self.weights = self.engine.compute_weights(eligible)
                self._save_history()

        # Apply weights to current signals
        weighted_score = 0.0
        used_signals   = {}
        for name, score in current_signals.items():
            w = self.weights.get(name, 1.0 / max(len(current_signals), 1))
            weighted_score += w * score
            used_signals[name] = {"score": round(score, 4), "weight": round(w, 4)}
            # Record prediction for IC tracking
            self.record_prediction(name, score)

        # Convert weighted score to signal
        threshold = 0.15
        if weighted_score > threshold:
            signal = "buy"
        elif weighted_score < -threshold:
            signal = "sell"
        else:
            signal = "hold"

        # Kelly sizing
        p_win  = 0.5 + min(abs(weighted_score) * 0.3, 0.25)  # map score → win prob
        b_ratio = 1.5  # default reward/risk ratio
        all_returns = []
        for v in self.return_history.values():
            all_returns.extend(v[-50:])
        kelly_f = self.kelly.size(p_win, b_ratio, all_returns)

        # Information ratio
        n_eff = self.effective_n()
        ir    = self.information_ratio()

        return {
            "signal":        signal,
            "score":         round(weighted_score, 4),
            "kelly_fraction": round(kelly_f, 4),
            "n_signals":     len(current_signals),
            "n_independent": round(n_eff, 2),
            "ir_estimate":   round(ir, 4),
            "weights":       self.weights.copy(),
            "breakdown":     used_signals,
        }

    # ── High-level run() — collects signals from all strategies ────────────

    def run(self, symbol: str, price_history: list = None) -> dict:
        """
        Polls each attached strategy for its current signal on `symbol`,
        then combines them using the 11-step engine.
        """
        raw_signals = {}
        for strat in self.strategies:
            try:
                name = getattr(strat, "name", strat.__class__.__name__)
                label = self.SIGNAL_LABELS.get(name, name.lower().replace(" ", "_"))
                result = strat.get_signal(symbol) if hasattr(strat, "get_signal") else None
                if result and isinstance(result, dict):
                    score = result.get("score", 0.0)
                    direction = result.get("signal", "hold")
                    if direction == "sell":
                        score = -abs(score)
                    elif direction == "buy":
                        score = abs(score)
                    else:
                        score = 0.0
                    raw_signals[label] = score
            except Exception:
                pass

        if not raw_signals:
            return {"signal": "hold", "score": 0.0, "reason": "no signals available"}

        return self.combine(raw_signals)



# ─────────────────────────────────────────────
# Standalone signal function (no strategy objects needed)
# ─────────────────────────────────────────────

def combine_signals(signals: Dict[str, float],
                    history: Dict[str, List[float]] = None,
                    lookback_d: int = 20) -> dict:
    """
    Lightweight one-shot combination. No state required.
    signals:  {name: score}  — current signal scores (positive=bull, negative=bear)
    history:  {name: [past_realized_returns]}  — optional, for weight learning
    Returns combined signal dict.
    """
    engine = AlphaCombinationEngine(lookback_d=lookback_d)
    kelly  = EmpiricalKelly()

    if history and all(len(v) >= 5 for v in history.values()):
        weights = engine.compute_weights(history)
    else:
        n = len(signals)
        weights = {k: 1.0/n for k in signals} if n > 0 else {}

    weighted = sum(weights.get(k, 0.0) * v for k, v in signals.items())

    # Fundamental Law metrics
    n_signals = len(signals)
    avg_ic    = 0.05  # conservative default when no history
    if history:
        ics = []
        for name, hist in history.items():
            pred = signals.get(name, 0.0)
            if len(hist) >= 4:
                ic = _information_coefficient([pred] * len(hist), hist)
                ics.append(ic)
        if ics:
            avg_ic = _mean(ics)
    ir = avg_ic * math.sqrt(max(n_signals, 1))

    threshold = 0.15
    signal = "buy" if weighted > threshold else ("sell" if weighted < -threshold else "hold")

    all_returns = [r for v in (history or {}).values() for r in v[-30:]]
    p_win  = 0.5 + min(abs(weighted) * 0.3, 0.25)
    kelly_f = kelly.size(p_win, 1.5, all_returns)

    return {
        "signal":          signal,
        "score":           round(weighted, 4),
        "kelly_fraction":  round(kelly_f, 4),
        "n_signals":       n_signals,
        "avg_ic":          round(avg_ic, 4),
        "ir_estimate":     round(ir, 4),
        "weights":         {k: round(v, 4) for k, v in weights.items()},
        "fundamental_law": f"IR = {avg_ic:.3f} × √{n_signals} = {ir:.3f}",
    }
