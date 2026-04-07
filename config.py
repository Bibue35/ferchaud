"""Central configuration — loads .env and exposes typed settings."""
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _list(key: str, default: str = "") -> List[str]:
    raw = os.getenv(key, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _pairs(key: str) -> List[Tuple[str, str]]:
    raw = os.getenv(key, "")
    pairs = []
    for pair_str in raw.split("|"):
        parts = [p.strip() for p in pair_str.split(",") if p.strip()]
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def _opt_float(key: str) -> Optional[float]:
    v = os.getenv(key)
    return float(v) if v is not None else None


@dataclass
class Config:
    # ── Alpaca ────────────────────────────────────────────────────────────────
    api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    trading_mode: str = field(default_factory=lambda: os.getenv("TRADING_MODE", "paper").lower())

    # ── Risk ──────────────────────────────────────────────────────────────────
    max_portfolio_risk: float = field(default_factory=lambda: _float("MAX_PORTFOLIO_RISK", 0.02))
    max_drawdown: float = field(default_factory=lambda: _float("MAX_DRAWDOWN", 0.15))
    max_position_size: float = field(default_factory=lambda: _float("MAX_POSITION_SIZE", 0.10))
    kelly_fraction: float = field(default_factory=lambda: _float("KELLY_FRACTION", 0.25))
    var_confidence: float = field(default_factory=lambda: _float("VAR_CONFIDENCE", 0.99))
    vol_target: float = field(default_factory=lambda: _float("VOL_TARGET", 0.12))
    vol_lookback: int = field(default_factory=lambda: _int("VOL_LOOKBACK", 60))
    max_leverage: float = field(default_factory=lambda: _float("MAX_LEVERAGE", 2.0))
    min_leverage: float = field(default_factory=lambda: _float("MIN_LEVERAGE", 0.25))
    max_correlation: float = field(default_factory=lambda: _float("MAX_CORRELATION", 0.85))

    # ── Regime Detection ──────────────────────────────────────────────────────
    regime_enabled: bool = field(default_factory=lambda: _bool("REGIME_ENABLED", True))
    regime_states: int = field(default_factory=lambda: _int("REGIME_STATES", 3))
    regime_lookback: int = field(default_factory=lambda: _int("REGIME_LOOKBACK", 504))
    regime_retrain_days: int = field(default_factory=lambda: _int("REGIME_RETRAIN_DAYS", 21))
    regime_market_proxy: str = field(default_factory=lambda: os.getenv("REGIME_MARKET_PROXY", "SPY"))
    regime_crisis_scale: float = field(default_factory=lambda: _float("REGIME_CRISIS_SCALE", 0.3))

    # ── VPIN ──────────────────────────────────────────────────────────────────
    vpin_enabled: bool = field(default_factory=lambda: _bool("VPIN_ENABLED", True))
    vpin_bucket_fraction: float = field(default_factory=lambda: _float("VPIN_BUCKET_FRACTION", 0.02))
    vpin_window: int = field(default_factory=lambda: _int("VPIN_WINDOW", 50))
    vpin_halt_threshold: float = field(default_factory=lambda: _float("VPIN_HALT_THRESHOLD", 0.95))

    # ── Strategies ────────────────────────────────────────────────────────────
    use_mean_reversion: bool = field(default_factory=lambda: _bool("STRATEGY_MEAN_REVERSION", True))
    use_momentum: bool = field(default_factory=lambda: _bool("STRATEGY_MOMENTUM", True))
    use_stat_arb: bool = field(default_factory=lambda: _bool("STRATEGY_STAT_ARB", True))
    use_market_making: bool = field(default_factory=lambda: _bool("STRATEGY_MARKET_MAKING", False))
    use_multi_factor: bool = field(default_factory=lambda: _bool("STRATEGY_MULTI_FACTOR", True))
    use_scalper: bool = field(default_factory=lambda: _bool("STRATEGY_SCALPER", True))
    use_catalyst: bool = field(default_factory=lambda: _bool("STRATEGY_CATALYST", True))
    use_aggressive_breakout: bool = field(default_factory=lambda: _bool("STRATEGY_AGGRESSIVE_BREAKOUT", True))

    # ── Virtual Trading Cap ───────────────────────────────────────────────────
    virtual_cap: float = field(default_factory=lambda: _float("VIRTUAL_CAP", 5000.0))

    # ── xAI / Grok API ───────────────────────────────────────────────────────
    xai_api_key: str = field(default_factory=lambda: os.getenv("XAI_API_KEY", ""))

    # Fixed strategy weights — None means use risk-parity
    weight_mean_reversion: Optional[float] = field(
        default_factory=lambda: _opt_float("STRATEGY_WEIGHT_MEAN_REVERSION"))
    weight_momentum: Optional[float] = field(
        default_factory=lambda: _opt_float("STRATEGY_WEIGHT_MOMENTUM"))
    weight_stat_arb: Optional[float] = field(
        default_factory=lambda: _opt_float("STRATEGY_WEIGHT_STAT_ARB"))
    weight_multi_factor: Optional[float] = field(
        default_factory=lambda: _opt_float("STRATEGY_WEIGHT_MULTI_FACTOR"))

    # ── Universes ─────────────────────────────────────────────────────────────
    stock_universe: List[str] = field(default_factory=lambda: _list(
        "STOCK_UNIVERSE", "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,SPY,QQQ,AMD"
    ))
    crypto_universe: List[str] = field(default_factory=lambda: _list(
        "CRYPTO_UNIVERSE", "BTC/USD,ETH/USD,SOL/USD"
    ))
    stat_arb_pairs: List[Tuple[str, str]] = field(default_factory=lambda: _pairs("STAT_ARB_PAIRS"))

    # ── Execution ─────────────────────────────────────────────────────────────
    order_type: str = field(default_factory=lambda: os.getenv("ORDER_TYPE", "smart"))
    slippage_tolerance: float = field(default_factory=lambda: _float("SLIPPAGE_TOLERANCE", 0.001))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    twap_slices: int = field(default_factory=lambda: _int("TWAP_SLICES", 5))
    twap_interval_sec: int = field(default_factory=lambda: _int("TWAP_INTERVAL_SEC", 30))
    vwap_participation: float = field(default_factory=lambda: _float("VWAP_PARTICIPATION", 0.10))

    # ── Backtesting ───────────────────────────────────────────────────────────
    backtest_enabled: bool = field(default_factory=lambda: _bool("BACKTEST_ENABLED", False))
    backtest_start: str = field(default_factory=lambda: os.getenv("BACKTEST_START", "2023-01-01"))
    backtest_end: str = field(default_factory=lambda: os.getenv("BACKTEST_END", "2025-12-31"))
    backtest_is_window: int = field(default_factory=lambda: _int("BACKTEST_IS_WINDOW", 252))
    backtest_oos_window: int = field(default_factory=lambda: _int("BACKTEST_OOS_WINDOW", 63))
    backtest_initial_capital: float = field(
        default_factory=lambda: _float("BACKTEST_INITIAL_CAPITAL", 100000))

    # ── ML ────────────────────────────────────────────────────────────────────
    ml_retrain_days: int = field(default_factory=lambda: _int("ML_RETRAIN_DAYS", 30))
    ml_min_samples: int = field(default_factory=lambda: _int("ML_MIN_SAMPLES", 500))
    ml_purge_days: int = field(default_factory=lambda: _int("ML_PURGE_DAYS", 5))
    ml_n_features: int = field(default_factory=lambda: _int("ML_N_FEATURES", 25))

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"

    @property
    def base_url(self) -> str:
        if self.is_paper:
            return "https://paper-api.alpaca.markets"
        return "https://api.alpaca.markets"

    @property
    def all_symbols(self) -> List[str]:
        return list(dict.fromkeys(self.stock_universe + self.crypto_universe))

    @property
    def has_fixed_weights(self) -> bool:
        return any(w is not None for w in [
            self.weight_mean_reversion, self.weight_momentum,
            self.weight_stat_arb, self.weight_multi_factor,
        ])

    def validate(self) -> None:
        if not self.api_key or not self.secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")
        if self.trading_mode not in ("paper", "live"):
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")


CONFIG = Config()
