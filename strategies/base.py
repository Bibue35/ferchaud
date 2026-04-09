"""Abstract base class for all strategies — regime + VPIN aware."""
from abc import ABC, abstractmethod
from typing import List, Optional

from core.executor import OrderExecutor
from core.portfolio import Portfolio
from core.regime import RegimeDetector
from core.risk import RiskEngine
from core.vpin import VPINMonitor
from data.feed import MarketDataFeed
from utils.logger import get_logger


class BaseStrategy(ABC):
    name: str = "base"

    def __init__(
        self,
        feed: MarketDataFeed,
        executor: OrderExecutor,
        portfolio: Portfolio,
        risk: RiskEngine,
        regime: Optional[RegimeDetector] = None,
        vpin: Optional[VPINMonitor] = None,
    ) -> None:
        self.feed = feed
        self.executor = executor
        self.portfolio = portfolio
        self.risk = risk
        self.regime = regime
        self.vpin = vpin
        self.log = get_logger(f"strategy.{self.name}")

    @abstractmethod
    def get_symbols(self) -> List[str]:
        """Return the list of symbols this strategy trades."""

    def update_symbols(self, symbols: List[str]) -> None:
        """Dynamically expand the symbol universe from the full market scanner.

        Called each cycle with the merged list of hot scanner picks + static
        config universe.  Subclasses that maintain their own symbol list should
        override this to merge/replace it; the default is a no-op so existing
        strategies continue to work unchanged.
        """
        pass

    @abstractmethod
    def run(self) -> None:
        """Execute one iteration of the strategy logic."""

    # ── Shared helpers ────────────────────────────────────────────────────────

    @property
    def regime_scale(self) -> float:
        if self.regime is not None:
            return self.regime.position_scale
        return 1.0

    def is_symbol_toxic(self, symbol: str) -> bool:
        if self.vpin is not None:
            return self.vpin.is_toxic(symbol)
        return False

    _cached_pending_symbols: set = set()
    _cached_pending_ts: float = 0.0

    def _refresh_pending_cache(self) -> None:
        """Cache pending orders for 5 seconds — avoids API call per symbol."""
        import time
        now = time.time()
        if now - self._cached_pending_ts < 5.0:
            return
        try:
            open_orders = self.executor._broker.list_open_orders()
            self.__class__._cached_pending_symbols = {o["symbol"] for o in open_orders}
        except Exception:
            self.__class__._cached_pending_symbols = set()
        self.__class__._cached_pending_ts = now

    def has_pending_order(self, symbol: str) -> bool:
        """Check if there's already a pending order (uses 5s cache)."""
        self._refresh_pending_cache()
        return symbol in self._cached_pending_symbols

    def should_skip_entry(self, symbol: str) -> bool:
        """Combined gate: risk halted OR VPIN toxic OR pending order exists."""
        if self.risk.is_halted:
            return True
        if self.has_pending_order(symbol):
            self.log.debug("Skipping %s — pending order exists", symbol)
            return True
        if self.is_symbol_toxic(symbol):
            self.log.debug("Skipping %s — VPIN toxic", symbol)
            return True
        return False

    def _kelly_qty(self, symbol: str, price: float, win_rate: float = 0.55,
                   avg_win: float = 0.02, avg_loss: float = 0.01) -> float:
        return self.risk.kelly_size(
            self.portfolio.equity, win_rate, avg_win, avg_loss, price
        )
