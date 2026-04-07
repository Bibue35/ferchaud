"""
Hidden Markov Model (HMM) Regime Detection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Identifies market regimes from price data to adapt strategy behaviour.

States (3-state model):
  0 = BULL   — high returns, low volatility → full risk
  1 = SIDEWAYS — low returns, moderate vol → reduce risk
  2 = CRISIS — negative returns, high vol → minimal risk

The model is trained on a rolling window of log returns + realized vol
(multivariate observations). States are sorted by ascending volatility
after each retrain to prevent label-switching.

Usage:
    regime = RegimeDetector(feed)
    regime.fit()                   # initial training
    state = regime.current_state   # 0, 1, or 2
    scale = regime.position_scale  # 1.0, 0.6, or 0.3
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

from config import CONFIG
from data.feed import MarketDataFeed
from utils.logger import get_logger

log = get_logger("core.regime")

BULL = 0
SIDEWAYS = 1
CRISIS = 2
STATE_NAMES = {BULL: "BULL", SIDEWAYS: "SIDEWAYS", CRISIS: "CRISIS"}


class RegimeDetector:
    def __init__(self, feed: MarketDataFeed) -> None:
        self._feed = feed
        self._model: Optional[GaussianHMM] = None
        self._state: int = BULL
        self._state_probs: Optional[np.ndarray] = None
        self._last_train: Optional[datetime] = None
        self._state_map: Optional[dict] = None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_state(self) -> int:
        return self._state

    @property
    def state_name(self) -> str:
        return STATE_NAMES.get(self._state, "UNKNOWN")

    @property
    def state_probabilities(self) -> Optional[np.ndarray]:
        return self._state_probs

    @property
    def position_scale(self) -> float:
        """Multiplicative scale factor for position sizes based on regime."""
        if self._state == CRISIS:
            return CONFIG.regime_crisis_scale
        if self._state == SIDEWAYS:
            return 0.85  # Was 0.6 — too conservative, still trade aggressively
        return 1.0

    def needs_retrain(self) -> bool:
        if self._model is None or self._last_train is None:
            return True
        days_since = (datetime.now(timezone.utc) - self._last_train).days
        return days_since >= CONFIG.regime_retrain_days

    # ── Training ──────────────────────────────────────────────────────────────

    def fit(self) -> bool:
        """Train or retrain the HMM on the market proxy."""
        obs = self._build_observations()
        if obs is None:
            log.warning("Insufficient data for regime model — defaulting to BULL")
            return False

        n_states = CONFIG.regime_states
        best_model = None
        best_score = -np.inf

        for seed in range(20):
            try:
                model = GaussianHMM(
                    n_components=n_states,
                    covariance_type="full",
                    n_iter=500,
                    random_state=seed,
                    tol=1e-4,
                )
                model.fit(obs)
                score = model.score(obs)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception:
                continue

        if best_model is None:
            log.error("HMM training failed across all seeds")
            return False

        self._model = best_model
        self._last_train = datetime.now(timezone.utc)
        self._state_map = self._sort_states_by_vol()
        self._update_state(obs)

        log.info(
            "Regime model trained — %d states | current: %s (p=%.2f)",
            n_states, self.state_name,
            self._state_probs[self._state] if self._state_probs is not None else 0,
        )
        self._log_state_params()
        return True

    def update(self) -> None:
        """Re-decode the current state with latest data (no retraining)."""
        if self._model is None:
            return
        obs = self._build_observations()
        if obs is not None:
            self._update_state(obs)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _build_observations(self) -> Optional[np.ndarray]:
        """Build multivariate observation matrix: [log_return, realized_vol]."""
        lookback_days = CONFIG.regime_lookback
        # Convert trading days to calendar days (approx 1.5x)
        calendar_days = int(lookback_days * 1.5) + 30
        start = datetime.now(timezone.utc) - timedelta(days=calendar_days)

        df = self._feed.get_bars(
            CONFIG.regime_market_proxy,
            timeframe="1D",
            limit=lookback_days,
            start=start,
        )
        if df.empty or len(df) < 120:
            log.warning("Regime got %d bars (need 120+)", len(df) if not df.empty else 0)
            return None

        close = df["close"]
        log_ret = np.log(close / close.shift(1)).dropna()
        rvol = log_ret.rolling(20).std() * np.sqrt(252)
        rvol = rvol.dropna()

        aligned = pd.concat([log_ret, rvol], axis=1).dropna()
        aligned.columns = ["ret", "rvol"]

        if len(aligned) < 100:
            return None

        return aligned.values

    def _sort_states_by_vol(self) -> dict:
        """Sort raw HMM states by ascending mean volatility."""
        means = self._model.means_
        vol_order = np.argsort(means[:, 1])
        return {raw: sorted_idx for sorted_idx, raw in enumerate(vol_order)}

    def _update_state(self, obs: np.ndarray) -> None:
        probs = self._model.predict_proba(obs)
        last_probs = probs[-1]
        mapped_probs = np.zeros(len(last_probs))
        for raw, sorted_idx in self._state_map.items():
            mapped_probs[sorted_idx] = last_probs[raw]
        self._state_probs = mapped_probs
        self._state = int(np.argmax(mapped_probs))

    def _log_state_params(self) -> None:
        if self._model is None:
            return
        for raw_state in range(self._model.n_components):
            sorted_state = self._state_map[raw_state]
            name = STATE_NAMES.get(sorted_state, f"S{sorted_state}")
            mu_ret = self._model.means_[raw_state, 0]
            mu_vol = self._model.means_[raw_state, 1]
            log.info(
                "  State %s: E[ret]=%.4f  E[vol]=%.2f%%",
                name, mu_ret, mu_vol * 100,
            )
