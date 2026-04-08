"""
K-Nearest Neighbors ML Strategy — Vault Strategy #72
Predicts next-T-day return based on historical 'similar' market states.
Feature vector = normalized price + volume moving averages of various lengths.
Euclidean distance finds k most similar historical periods.
Signal = average realized return of those k nearest neighbors.

Reference: Kakushadze & Serur, "151 Trading Strategies," Section 3.17
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional


class KNNStrategy:
    """
    Single-stock KNN price prediction.
    Vault Strategy #72: Machine Learning – single-stock KNN.
    """
    name        = "KNNMachineLearning"
    description = "K-nearest neighbors return predictor using price/volume features (Vault #72)."
    max_positions     = 8
    position_size_pct = 0.05

    def __init__(self, k: int = None, prediction_horizon: int = 5,
                 feature_windows: tuple = (5, 10, 20, 50),
                 entry_threshold: float = 0.005,
                 exit_threshold: float = 0.001):
        """
        k: number of nearest neighbors (default: sqrt(sample_size))
        prediction_horizon: days ahead to predict
        feature_windows: MA windows for feature construction
        entry_threshold: predicted return must exceed this to trade
        exit_threshold: exit when prediction drops below this
        """
        self.k                  = k
        self.prediction_horizon = prediction_horizon
        self.feature_windows    = feature_windows
        self.entry_threshold    = entry_threshold
        self.exit_threshold     = exit_threshold

    # ── Feature engineering ───────────────────────────────────────────────────

    def _build_features(self, bars: pd.DataFrame) -> pd.DataFrame:
        """
        Construct feature matrix X from price/volume MAs.
        Each row = one historical observation.
        Features: price_ma_ratio (close / MA_w) and vol_ma_ratio for each window w.
        """
        close  = bars["close"].astype(float)
        volume = bars["volume"].astype(float) if "volume" in bars.columns else pd.Series(
            np.ones(len(close)), index=close.index)

        features = {}
        for w in self.feature_windows:
            ma_price = close.rolling(w).mean()
            ma_vol   = volume.rolling(w).mean()
            features[f"price_ma{w}"] = close / ma_price.replace(0, np.nan)
            features[f"vol_ma{w}"]   = volume / ma_vol.replace(0, np.nan)

        # Add lagged returns as features
        for lag in [1, 2, 5]:
            features[f"ret_lag{lag}"] = close.pct_change(lag)

        df = pd.DataFrame(features)
        return df

    def _build_targets(self, close: pd.Series) -> pd.Series:
        """
        Target Y(t) = cumulative return over next prediction_horizon days.
        Y(t) = close(t + horizon) / close(t) - 1
        """
        return close.pct_change(self.prediction_horizon).shift(-self.prediction_horizon)

    # ── Standardize features ──────────────────────────────────────────────────

    @staticmethod
    def _standardize(X: np.ndarray) -> np.ndarray:
        """Z-score each feature column independently."""
        mu  = np.nanmean(X, axis=0)
        std = np.nanstd(X, axis=0)
        std[std == 0] = 1.0
        return (X - mu) / std

    # ── KNN prediction ────────────────────────────────────────────────────────

    def predict(self, bars: pd.DataFrame) -> dict:
        """
        Generate KNN signal for the most recent bar.

        Returns:
            signal:    "buy" | "sell" | "hold"
            score:     predicted return (magnitude = confidence)
            k_used:    actual k used
            n_samples: training sample size
        """
        min_bars = max(self.feature_windows) + self.prediction_horizon + 30
        if bars is None or len(bars) < min_bars:
            return {"signal": "hold", "score": 0.0, "k_used": 0, "n_samples": 0}

        feat_df = self._build_features(bars)
        targets = self._build_targets(bars["close"].astype(float))

        # Drop rows with NaN in either features or targets
        combined = pd.concat([feat_df, targets.rename("target")], axis=1).dropna()
        if len(combined) < 20:
            return {"signal": "hold", "score": 0.0, "k_used": 0, "n_samples": 0}

        X_all = combined[feat_df.columns].values
        Y_all = combined["target"].values

        # Training set = all rows except the last (which is the "current" state)
        X_train = X_all[:-1]
        Y_train = Y_all[:-1]
        X_query = X_all[-1:].reshape(1, -1)

        # Standardize
        X_std   = self._standardize(np.vstack([X_train, X_query]))
        X_tr_s  = X_std[:-1]
        X_q_s   = X_std[-1:]

        n = len(X_tr_s)
        k = self.k or max(3, int(np.sqrt(n)))

        # Euclidean distances
        diffs = X_tr_s - X_q_s
        dists = np.sqrt((diffs ** 2).sum(axis=1))

        # k nearest neighbors
        nn_idx     = np.argsort(dists)[:k]
        nn_returns = Y_train[nn_idx]
        predicted  = float(np.mean(nn_returns))

        # Optional: weighted by inverse distance
        weights      = 1.0 / (dists[nn_idx] + 1e-8)
        weighted_pred = float(np.average(nn_returns, weights=weights))

        # Use weighted prediction as primary signal
        pred = weighted_pred

        signal = "hold"
        if pred > self.entry_threshold:
            signal = "buy"
        elif pred < -self.entry_threshold:
            signal = "sell"

        # Neighbor agreement ratio (confidence measure)
        n_bullish = int((nn_returns > 0).sum())
        agreement = n_bullish / k

        return {
            "signal":        signal,
            "score":         round(pred, 6),
            "pred_simple":   round(predicted, 6),
            "pred_weighted": round(weighted_pred, 6),
            "k_used":        k,
            "n_samples":     n,
            "agreement_pct": round(agreement * 100, 1),
            "nn_return_std": round(float(np.std(nn_returns)), 6),
        }

    # ── Backtest-safe incremental predict ─────────────────────────────────────

    def batch_predict(self, bars: pd.DataFrame,
                      min_train: int = 100) -> pd.Series:
        """
        Walk-forward prediction over full history (for backtesting).
        Returns pd.Series of predicted returns aligned with bars index.
        """
        predictions = pd.Series(np.nan, index=bars.index)
        for i in range(min_train, len(bars)):
            result = self.predict(bars.iloc[:i])
            predictions.iloc[i] = result["score"]
        return predictions
