"""
Multi-Factor Alpha Strategy with ML Boosting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The flagship strategy. Combines ~25 engineered features into a LightGBM
model that predicts forward 5-day returns cross-sectionally.

Pipeline:
  1. Build feature matrix for each symbol (data/indicators.build_feature_matrix)
  2. Target: triple-barrier label (profit-take, stop-loss, max-hold)
  3. Train via purged walk-forward cross-validation
  4. Score universe cross-sectionally → rank → long top quintile, short bottom
  5. Size via Bayesian Kelly × regime scale × vol-target leverage

Retrains monthly (CONFIG.ml_retrain_days). Uses walk-forward: never sees
future data during training.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from config import CONFIG
from data.feed import MarketDataFeed
from data.indicators import build_feature_matrix, atr
from strategies.base import BaseStrategy
from utils.logger import get_logger

log = get_logger("strategy.multi_factor")

# Try to import LightGBM; gracefully degrade if not installed
try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    log.warning("LightGBM not installed — multi-factor strategy will use linear fallback")


# ═══════════════════════════════════════════════════════════════════════════════
#  Triple-Barrier Labelling
# ═══════════════════════════════════════════════════════════════════════════════

def triple_barrier_labels(
    close: pd.Series,
    atr_series: pd.Series,
    tp_mult: float = 2.0,
    sl_mult: float = 1.5,
    max_hold: int = 10,
) -> pd.Series:
    """
    Label each bar based on which barrier is hit first:
      +1 = profit-take hit first (bullish)
      -1 = stop-loss hit first (bearish)
       0 = max holding period expired without hitting either
    """
    labels = pd.Series(0, index=close.index)

    for i in range(len(close) - max_hold):
        entry = close.iloc[i]
        entry_atr = atr_series.iloc[i]
        if np.isnan(entry_atr) or entry_atr <= 0:
            continue

        tp = entry + tp_mult * entry_atr
        sl = entry - sl_mult * entry_atr

        for j in range(1, max_hold + 1):
            if i + j >= len(close):
                break
            price = close.iloc[i + j]
            if price >= tp:
                labels.iloc[i] = 1
                break
            elif price <= sl:
                labels.iloc[i] = -1
                break

    return labels


# ═══════════════════════════════════════════════════════════════════════════════
#  Model Wrapper
# ═══════════════════════════════════════════════════════════════════════════════

class AlphaModel:
    """Wraps LightGBM training and prediction with purged CV."""

    def __init__(self) -> None:
        self._model = None
        self._feature_names: List[str] = []
        self._last_train: Optional[datetime] = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def needs_retrain(self) -> bool:
        if self._model is None or self._last_train is None:
            return True
        days = (datetime.now(timezone.utc) - self._last_train).days
        return days >= CONFIG.ml_retrain_days

    def train(self, X: pd.DataFrame, y: pd.Series) -> dict:
        """
        Train with purged time-series cross-validation.

        Returns dict with in-sample and OOS metrics.
        """
        if len(X) < CONFIG.ml_min_samples:
            log.warning("Insufficient samples (%d < %d) — skipping ML training",
                        len(X), CONFIG.ml_min_samples)
            return {}

        self._feature_names = list(X.columns)
        purge = CONFIG.ml_purge_days

        # Purged time-series split
        tscv = TimeSeriesSplit(n_splits=5)
        oos_scores = []

        for train_idx, test_idx in tscv.split(X):
            # Purge: remove training samples too close to test boundary
            test_start = test_idx[0]
            train_idx = train_idx[train_idx < test_start - purge]
            if len(train_idx) < 100:
                continue

            X_train = X.iloc[train_idx]
            y_train = y.iloc[train_idx]
            X_test = X.iloc[test_idx]
            y_test = y.iloc[test_idx]

            if HAS_LGB:
                model = self._train_lgb(X_train, y_train, X_test, y_test)
                preds = model.predict(X_test)
            else:
                model, coefs = self._train_linear(X_train, y_train)
                preds = X_test.values @ coefs

            # Spearman IC (rank correlation)
            from scipy.stats import spearmanr
            ic, _ = spearmanr(preds, y_test)
            oos_scores.append(ic)

        avg_ic = np.mean(oos_scores) if oos_scores else 0.0
        log.info("ML cross-validation — avg IC=%.4f across %d folds", avg_ic, len(oos_scores))

        # Final model on all data
        if HAS_LGB:
            self._model = self._train_lgb(X, y)
        else:
            self._model, _ = self._train_linear(X, y)

        self._last_train = datetime.now(timezone.utc)
        return {"avg_ic": avg_ic, "n_samples": len(X), "n_features": len(self._feature_names)}

    def predict(self, X: pd.DataFrame) -> pd.Series:
        if self._model is None:
            return pd.Series(0.0, index=X.index)

        # Ensure feature alignment
        X_aligned = X.reindex(columns=self._feature_names, fill_value=0.0)

        if HAS_LGB:
            preds = self._model.predict(X_aligned)
        else:
            preds = X_aligned.values @ self._model
        return pd.Series(preds, index=X.index, name="alpha")

    def feature_importance(self) -> Dict[str, float]:
        if not HAS_LGB or self._model is None:
            return {}
        imp = self._model.feature_importance(importance_type="gain")
        total = imp.sum()
        if total == 0:
            return {}
        return {
            name: float(val / total)
            for name, val in zip(self._feature_names, imp)
        }

    @staticmethod
    def _train_lgb(X_train, y_train, X_val=None, y_val=None):
        params = {
            "objective": "regression",
            "metric": "mae",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.7,
            "bagging_fraction": 0.7,
            "bagging_freq": 5,
            "min_child_samples": 100,
            "lambda_l1": 0.1,
            "lambda_l2": 1.0,
            "max_depth": 6,
            "verbose": -1,
        }
        train_set = lgb.Dataset(X_train, y_train)
        valid_sets = [train_set]
        callbacks = [lgb.log_evaluation(0)]

        if X_val is not None and y_val is not None:
            val_set = lgb.Dataset(X_val, y_val, reference=train_set)
            valid_sets.append(val_set)
            callbacks.append(lgb.early_stopping(50, verbose=False))

        model = lgb.train(
            params,
            train_set,
            num_boost_round=500,
            valid_sets=valid_sets,
            callbacks=callbacks,
        )
        return model

    @staticmethod
    def _train_linear(X_train, y_train):
        """Ridge regression fallback when LightGBM isn't available."""
        from sklearn.linear_model import Ridge
        model = Ridge(alpha=1.0)
        model.fit(X_train, y_train)
        return model.coef_, model.coef_


# ═══════════════════════════════════════════════════════════════════════════════
#  Strategy
# ═══════════════════════════════════════════════════════════════════════════════

class MultiFactorStrategy(BaseStrategy):
    name = "multi_factor"

    TOP_QUINTILE = 0.2
    BOTTOM_QUINTILE = 0.2
    MAX_POSITIONS = 10
    ATR_STOP_MULT = 2.0
    ATR_TARGET_MULT = 3.0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._alpha_model = AlphaModel()
        self._market_returns: Optional[pd.Series] = None

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe + CONFIG.crypto_universe

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk engine halted — skipping multi-factor")
            return

        # Fetch market returns for beta computation
        if self._market_returns is None:
            self._load_market_returns()

        # Retrain model if needed
        if self._alpha_model.needs_retrain():
            self._retrain()

        if not self._alpha_model.is_trained:
            return

        # Score universe and trade
        self._score_and_trade()

    def _load_market_returns(self) -> None:
        df = self.feed.get_bars(CONFIG.regime_market_proxy, timeframe="1D", limit=300)
        if not df.empty:
            self._market_returns = df["close"].pct_change().dropna()

    def _retrain(self) -> None:
        self.log.info("Retraining ML alpha model…")
        all_X = []
        all_y = []

        for symbol in self.get_symbols():
            df = self.feed.get_bars(symbol, timeframe="1D", limit=CONFIG.backtest_is_window + 100)
            if df.empty or len(df) < 200:
                continue

            features = build_feature_matrix(df, self._market_returns)
            atr_vals = atr(df["high"], df["low"], df["close"], 14)

            # Triple-barrier labels as target
            labels = triple_barrier_labels(
                df["close"], atr_vals,
                tp_mult=self.ATR_TARGET_MULT,
                sl_mult=self.ATR_STOP_MULT,
                max_hold=10,
            )

            # Align
            common = features.index.intersection(labels.index)
            if len(common) < 100:
                continue

            features = features.loc[common]
            labels = labels.loc[common]

            # Forward 5-day return as regression target
            fwd = df["close"].pct_change(5).shift(-5).loc[common]
            valid = fwd.dropna().index.intersection(features.index)
            if len(valid) < 100:
                continue

            all_X.append(features.loc[valid])
            all_y.append(fwd.loc[valid])

        if not all_X:
            self.log.warning("No sufficient data for ML training")
            return

        X = pd.concat(all_X)
        y = pd.concat(all_y)

        metrics = self._alpha_model.train(X, y)
        if metrics:
            self.log.info(
                "ML model trained: IC=%.4f  samples=%d  features=%d",
                metrics.get("avg_ic", 0), metrics.get("n_samples", 0),
                metrics.get("n_features", 0),
            )
            imp = self._alpha_model.feature_importance()
            if imp:
                top5 = sorted(imp.items(), key=lambda x: x[1], reverse=True)[:5]
                self.log.info("Top features: %s", ", ".join(f"{k}={v:.3f}" for k, v in top5))

    def _score_and_trade(self) -> None:
        scores: Dict[str, float] = {}
        latest_data: Dict[str, dict] = {}

        for symbol in self.get_symbols():
            df = self.feed.get_bars(symbol, timeframe="1D", limit=300)
            if df.empty or len(df) < 100:
                continue

            features = build_feature_matrix(df, self._market_returns)
            if features.empty:
                continue

            # Score = predicted forward return
            alpha = self._alpha_model.predict(features.iloc[[-1]])
            score = alpha.iloc[0]
            scores[symbol] = score

            atr_val = atr(df["high"], df["low"], df["close"], 14).iloc[-1]
            latest_data[symbol] = {
                "price": df["close"].iloc[-1],
                "atr": atr_val,
            }

        if not scores:
            return

        # Cross-sectional ranking
        sorted_symbols = sorted(scores.items(), key=lambda x: x[1])
        n = len(sorted_symbols)
        n_long = max(1, int(n * self.TOP_QUINTILE))
        n_short = max(1, int(n * self.BOTTOM_QUINTILE))

        longs = [s for s, _ in sorted_symbols[-n_long:] if scores[s] > 0]
        shorts = [s for s, _ in sorted_symbols[:n_short] if scores[s] < 0]

        # Cap positions
        longs = longs[:self.MAX_POSITIONS // 2]
        shorts = shorts[:self.MAX_POSITIONS // 2]

        self.log.info(
            "ML scores — longs: %s  shorts: %s",
            [(s, f"{scores[s]:.4f}") for s in longs],
            [(s, f"{scores[s]:.4f}") for s in shorts],
        )

        # Close positions no longer in target
        for symbol in list(self.portfolio.positions.keys()):
            if symbol not in longs and symbol not in shorts:
                pos_side = self.portfolio.position_side(symbol)
                # Only close positions managed by this strategy (check tag in a real system)
                if pos_side == "long" and symbol not in longs:
                    self.executor.exit_position(symbol, reason="MF: no longer in top quintile")
                elif pos_side == "short" and symbol not in shorts:
                    self.executor.exit_position(symbol, reason="MF: no longer in bottom quintile")

        # Enter longs
        for symbol in longs:
            if self.portfolio.has_position(symbol):
                continue
            data = latest_data.get(symbol)
            if not data:
                continue

            stop, target = self.risk.atr_stops(
                data["price"], data["atr"], "buy",
                self.ATR_STOP_MULT, self.ATR_TARGET_MULT,
            )
            stop_dist = abs(data["price"] - stop)
            rs = self.regime_scale
            # Confidence from normalized ML score: top quintile ranges 0→1
            raw_score = scores.get(symbol, 0.0)
            max_score = max(abs(s) for s in scores.values()) if scores else 1.0
            confidence = float(min(0.5 + abs(raw_score) / (max_score + 1e-9) * 0.5, 1.0))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, data["price"], stop_dist,
                regime_scale=rs, confidence=confidence,
            )
            if qty >= 1:
                self.executor.enter_long(
                    symbol, qty, stop_price=stop,
                    take_profit=target, strategy_tag="MF",
                    confidence=confidence,
                )

        # Enter shorts
        for symbol in shorts:
            if self.portfolio.has_position(symbol):
                continue
            data = latest_data.get(symbol)
            if not data:
                continue

            stop, target = self.risk.atr_stops(
                data["price"], data["atr"], "sell",
                self.ATR_STOP_MULT, self.ATR_TARGET_MULT,
            )
            stop_dist = abs(data["price"] - stop)
            rs = self.regime_scale
            raw_score = scores.get(symbol, 0.0)
            max_score = max(abs(s) for s in scores.values()) if scores else 1.0
            confidence = float(min(0.5 + abs(raw_score) / (max_score + 1e-9) * 0.5, 1.0))
            qty = self.risk.risk_based_size(
                self.portfolio.equity, data["price"], stop_dist,
                regime_scale=rs, confidence=confidence,
            )
            if qty >= 1:
                self.executor.enter_short(
                    symbol, qty, stop_price=stop,
                    take_profit=target, strategy_tag="MF",
                    confidence=confidence,
                )
