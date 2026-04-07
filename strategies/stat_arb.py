"""
Statistical Arbitrage — Pairs Trading (v2 — Regime + VPIN aware)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Method:
  1. Test pairs for cointegration (Engle-Granger, p < 0.05)
  2. Compute spread = leg_A - hedge_ratio × leg_B
  3. Model spread as mean-reverting (Ornstein-Uhlenbeck)
  4. Enter when |z-score| > entry_z, exit when |z-score| < exit_z
  5. Half-life filter: only trade pairs with HL 2–60 days

v2 enhancements:
  • VPIN filter: skip entry if either leg shows toxic flow
  • Regime-scaled sizing (crisis = 30%)
  • Hard z-score stop-loss at |z| > 4.0
"""
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.stattools import coint

from config import CONFIG
from data.indicators import zscore
from strategies.base import BaseStrategy

warnings.filterwarnings("ignore", category=FutureWarning)

ENTRY_Z = 2.0
EXIT_Z = 0.5
STOP_Z = 4.0
COINT_PVALUE = 0.05
MIN_HALF_LIFE = 2
MAX_HALF_LIFE = 60
LOOKBACK_DAYS = 252
RECALIBRATE_EVERY = 20


def _half_life(spread: pd.Series) -> float:
    lag = spread.shift(1).dropna()
    delta = spread.diff().dropna()
    aligned = pd.concat([lag, delta], axis=1).dropna()
    if len(aligned) < 10:
        return float("inf")
    X = add_constant(aligned.iloc[:, 0])
    result = OLS(aligned.iloc[:, 1], X).fit()
    lam = result.params.iloc[1]
    if lam >= 0:
        return float("inf")
    return -np.log(2) / lam


def _hedge_ratio(y: pd.Series, x: pd.Series) -> float:
    X = add_constant(x)
    return OLS(y, X).fit().params.iloc[1]


class StatArbStrategy(BaseStrategy):
    name = "stat_arb"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pair_cache: Dict[Tuple[str, str], dict] = {}
        self._run_count = 0

    def get_symbols(self) -> List[str]:
        syms = set()
        for a, b in CONFIG.stat_arb_pairs:
            syms.add(a)
            syms.add(b)
        return list(syms)

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk engine halted — skipping stat-arb")
            return

        self._run_count += 1
        recalibrate = (self._run_count % RECALIBRATE_EVERY == 1)

        for pair in CONFIG.stat_arb_pairs:
            try:
                self._process_pair(pair, recalibrate=recalibrate)
            except Exception as exc:
                self.log.error("Error processing pair %s/%s: %s", pair[0], pair[1], exc)

    def _process_pair(self, pair: Tuple[str, str], recalibrate: bool) -> None:
        sym_a, sym_b = pair

        if recalibrate or pair not in self._pair_cache:
            calibration = self._calibrate(sym_a, sym_b)
            if calibration is None:
                return
            self._pair_cache[pair] = calibration
            self.log.info(
                "Pair %s/%s calibrated — HR=%.4f  HL=%.1fd  p=%.4f",
                sym_a, sym_b,
                calibration["hedge_ratio"],
                calibration["half_life"],
                calibration["coint_pvalue"],
            )

        cal = self._pair_cache.get(pair)
        if cal is None:
            return

        df_a = self.feed.get_bars(sym_a, timeframe="1D", limit=60)
        df_b = self.feed.get_bars(sym_b, timeframe="1D", limit=60)
        if df_a.empty or df_b.empty:
            return

        close_a, close_b = df_a["close"].align(df_b["close"], join="inner")
        if len(close_a) < 20:
            return

        spread = close_a - cal["hedge_ratio"] * close_b
        zs = zscore(spread, window=min(len(spread), 60))
        cur_z = zs.iloc[-1]

        if np.isnan(cur_z):
            return

        has_a = self.portfolio.has_position(sym_a)
        has_b = self.portfolio.has_position(sym_b)
        in_trade = has_a or has_b

        # ── Exit ──────────────────────────────────────────────────────────────
        if in_trade:
            # Mean-reversion exit
            if abs(cur_z) < EXIT_Z:
                self.log.info("SA EXIT %s/%s — z=%.2f (mean-reverted)", sym_a, sym_b, cur_z)
                self.executor.exit_position(sym_a, reason="SA: z-score mean-reverted")
                self.executor.exit_position(sym_b, reason="SA: z-score mean-reverted")
                return
            # Stop-loss: spread blew out
            if abs(cur_z) > STOP_Z:
                self.log.warning("SA STOP %s/%s — z=%.2f (exceeded %.1f)", sym_a, sym_b, cur_z, STOP_Z)
                self.executor.exit_position(sym_a, reason="SA: z-score stop-loss")
                self.executor.exit_position(sym_b, reason="SA: z-score stop-loss")
                return
            return

        # ── Entry gate ────────────────────────────────────────────────────────
        if self.should_skip_entry(sym_a) or self.should_skip_entry(sym_b):
            return

        scale = self.regime_scale
        price_a = close_a.iloc[-1]
        price_b = close_b.iloc[-1]

        # Confidence: z-score magnitude relative to entry threshold
        # z=2.0 (min entry) → conf=0.50, z=3.0 → conf=0.65, z=4.5 → conf=0.90
        z_abs = abs(cur_z)
        confidence = float(min(0.5 + (z_abs - ENTRY_Z) / (STOP_Z - ENTRY_Z) * 0.5, 0.95))

        alloc = self.portfolio.equity * CONFIG.max_position_size * 0.5 * scale
        # Scale allocation by confidence
        alloc *= (0.5 + confidence)
        qty_a = max(1, int(alloc / price_a))
        qty_b = max(1, int((qty_a * price_a * cal["hedge_ratio"]) / price_b))

        # Long spread: z < -ENTRY_Z → long A, short B
        if cur_z < -ENTRY_Z:
            self.log.info("SA LONG SPREAD %s/%s  z=%.2f  regime=%s(×%.1f)  conf=%.0f%%",
                          sym_a, sym_b, cur_z,
                          self.regime.state_name if self.regime else "N/A",
                          scale, confidence * 100)
            self.executor.enter_long(sym_a, qty_a, strategy_tag="SA", confidence=confidence)
            self.executor.enter_short(sym_b, qty_b, strategy_tag="SA", confidence=confidence)

        # Short spread: z > +ENTRY_Z → short A, long B
        elif cur_z > ENTRY_Z:
            self.log.info("SA SHORT SPREAD %s/%s  z=%.2f  regime=%s(×%.1f)  conf=%.0f%%",
                          sym_a, sym_b, cur_z,
                          self.regime.state_name if self.regime else "N/A",
                          scale, confidence * 100)
            self.executor.enter_short(sym_a, qty_a, strategy_tag="SA", confidence=confidence)
            self.executor.enter_long(sym_b, qty_b, strategy_tag="SA", confidence=confidence)

    def _calibrate(self, sym_a: str, sym_b: str) -> Optional[dict]:
        df_a = self.feed.get_bars(sym_a, timeframe="1D", limit=LOOKBACK_DAYS)
        df_b = self.feed.get_bars(sym_b, timeframe="1D", limit=LOOKBACK_DAYS)
        if df_a.empty or df_b.empty:
            return None

        close_a, close_b = df_a["close"].align(df_b["close"], join="inner")
        if len(close_a) < 60:
            return None

        _, pvalue, _ = coint(close_a, close_b)
        if pvalue > COINT_PVALUE:
            self.log.debug("Pair %s/%s not cointegrated (p=%.3f)", sym_a, sym_b, pvalue)
            return None

        hr = _hedge_ratio(close_a, close_b)
        spread = close_a - hr * close_b
        hl = _half_life(spread)

        if not (MIN_HALF_LIFE <= hl <= MAX_HALF_LIFE):
            self.log.debug("Pair %s/%s HL=%.1f outside [%d,%d]",
                           sym_a, sym_b, hl, MIN_HALF_LIFE, MAX_HALF_LIFE)
            return None

        return {"hedge_ratio": hr, "half_life": hl, "coint_pvalue": pvalue}
