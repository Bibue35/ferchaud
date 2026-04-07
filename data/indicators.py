"""
Technical Indicators Library
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pure numpy/pandas implementations — no external TA dependency at runtime.

Includes standard indicators + advanced quant features for the ML pipeline:
  • Parkinson & Garman-Klass volatility estimators
  • Realised skewness & kurtosis
  • Amihud illiquidity
  • Normalised price position (percentile rank)
  • Kyle's Lambda (price impact)
"""
import numpy as np
import pandas as pd
from typing import Tuple


# ═══════════════════════════════════════════════════════════════════════════════
#  Standard Indicators
# ═══════════════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, period)
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def bollinger_position(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    """Where price sits within Bollinger Bands: -1 (lower) to +1 (upper)."""
    upper, mid, lower = bollinger_bands(series, period, num_std)
    band_width = (upper - lower).replace(0, np.nan)
    return (series - mid) / (band_width / 2)


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_s
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def zscore(series: pd.Series, window: int = 60) -> pd.Series:
    roll_mean = series.rolling(window).mean()
    roll_std = series.rolling(window).std()
    return (series - roll_mean) / roll_std.replace(0, np.nan)


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical = (high + low + close) / 3
    return (typical * volume).cumsum() / volume.cumsum()


# ═══════════════════════════════════════════════════════════════════════════════
#  Advanced / Quant Features
# ═══════════════════════════════════════════════════════════════════════════════

def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def multi_horizon_returns(close: pd.Series) -> pd.DataFrame:
    """Returns at 1d, 5d, 10d, 21d, 63d horizons."""
    return pd.DataFrame({
        "ret_1d": close.pct_change(1),
        "ret_5d": close.pct_change(5),
        "ret_10d": close.pct_change(10),
        "ret_21d": close.pct_change(21),
        "ret_63d": close.pct_change(63),
    }, index=close.index)


def realised_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility."""
    return log_returns(close).rolling(window).std() * np.sqrt(252)


def parkinson_vol(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    """Parkinson estimator — uses high-low range, more efficient than close-close."""
    log_hl = np.log(high / low)
    return np.sqrt(
        (1 / (4 * window * np.log(2))) * (log_hl ** 2).rolling(window).sum()
    ) * np.sqrt(252)


def garman_klass_vol(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    window: int = 20
) -> pd.Series:
    """Garman-Klass estimator — uses OHLC for maximum efficiency."""
    log_hl = np.log(high / low)
    log_co = np.log(close / open_)
    gk = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    return np.sqrt(gk.rolling(window).mean() * 252)


def realised_skew(close: pd.Series, window: int = 20) -> pd.Series:
    return log_returns(close).rolling(window).skew()


def realised_kurtosis(close: pd.Series, window: int = 20) -> pd.Series:
    return log_returns(close).rolling(window).kurt()


def vol_of_vol(close: pd.Series, vol_window: int = 20, outer_window: int = 60) -> pd.Series:
    """Volatility of volatility — captures vol regime uncertainty."""
    rv = realised_vol(close, vol_window)
    return rv.rolling(outer_window).std()


def relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    """Current volume / average volume."""
    avg = volume.rolling(window).mean()
    return volume / avg.replace(0, np.nan)


def amihud_illiquidity(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    """Amihud illiquidity ratio — higher = less liquid."""
    dollar_vol = close * volume
    daily_impact = close.pct_change().abs() / dollar_vol.replace(0, np.nan)
    return daily_impact.rolling(window).mean()


def price_percentile_rank(close: pd.Series, window: int = 252) -> pd.Series:
    """Where current price sits in its trailing range [0, 1]."""
    return close.rolling(window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


def ma_ratio(close: pd.Series, fast: int = 10, slow: int = 50) -> pd.Series:
    """Fast MA / Slow MA - 1. Cross-through-zero signals crossover."""
    return sma(close, fast) / sma(close, slow) - 1


def vwap_deviation(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    """(price - VWAP) / VWAP — measures how far price has moved from fair value."""
    v = vwap(high, low, close, volume)
    return (close - v) / v.replace(0, np.nan)


def rolling_beta(
    asset_returns: pd.Series, market_returns: pd.Series, window: int = 60
) -> pd.Series:
    """Rolling beta to market."""
    cov = asset_returns.rolling(window).cov(market_returns)
    mkt_var = market_returns.rolling(window).var()
    return cov / mkt_var.replace(0, np.nan)


# ═══════════════════════════════════════════════════════════════════════════════
#  Feature Engineering Pipeline (for ML strategy)
# ═══════════════════════════════════════════════════════════════════════════════

def build_feature_matrix(
    df: pd.DataFrame, market_returns: pd.Series = None
) -> pd.DataFrame:
    """
    Build a full feature matrix from OHLCV data for ML models.

    Input: DataFrame with columns [open, high, low, close, volume]
    Output: DataFrame of ~25 features, all stationary, winsorised.
    """
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]

    features = pd.DataFrame(index=df.index)

    # Returns
    ret = multi_horizon_returns(c)
    for col in ret.columns:
        features[col] = ret[col]

    # Volatility features
    features["rvol_20"] = realised_vol(c, 20)
    features["rvol_60"] = realised_vol(c, 60)
    features["parkinson_vol"] = parkinson_vol(h, l, 20)
    features["gk_vol"] = garman_klass_vol(o, h, l, c, 20)
    features["vol_of_vol"] = vol_of_vol(c, 20, 60)

    # Higher moments
    features["rskew"] = realised_skew(c, 20)
    features["rkurt"] = realised_kurtosis(c, 20)

    # Trend / mean-reversion
    features["rsi_14"] = rsi(c, 14) / 100.0 - 0.5  # Centre around 0
    features["bb_pos"] = bollinger_position(c, 20, 2.0)
    features["ma_ratio_10_50"] = ma_ratio(c, 10, 50)
    features["ma_ratio_5_20"] = ma_ratio(c, 5, 20)
    features["adx_14"] = adx(h, l, c, 14) / 100.0

    # MACD
    macd_l, _, hist = macd(c)
    features["macd_hist"] = hist / c * 100  # Normalise

    # Volume
    features["rel_volume"] = relative_volume(v, 20)
    features["amihud"] = amihud_illiquidity(c, v, 20)
    features["vwap_dev"] = vwap_deviation(h, l, c, v)

    # Price position
    features["pct_rank_252"] = price_percentile_rank(c, 252)

    # ATR as fraction of price (normalised)
    atr_val = atr(h, l, c, 14)
    features["atr_pct"] = atr_val / c

    # Beta (if market returns provided)
    if market_returns is not None:
        asset_ret = c.pct_change()
        features["beta_60"] = rolling_beta(asset_ret, market_returns, 60)

    # Winsorise at ±5 sigma
    for col in features.columns:
        s = features[col]
        mu, sigma = s.mean(), s.std()
        if sigma > 0:
            features[col] = s.clip(mu - 5 * sigma, mu + 5 * sigma)

    return features.dropna()
