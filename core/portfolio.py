"""Portfolio manager — real-time P&L, exposure, and position tracking."""
from typing import Dict, Optional

from core.broker import Broker
from utils.logger import get_logger

log = get_logger("core.portfolio")


class Portfolio:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker
        self._initial_value: Optional[float] = None
        self.refresh()

    def refresh(self) -> None:
        try:
            account = self._broker.get_account()
            self._equity = account["equity"]
            self._cash = account["cash"]
            self._buying_power = account["buying_power"]
        except Exception as e:
            # Keep using cached values on transient API failure
            import logging
            logging.getLogger("core.portfolio").warning("get_account() failed: %s — using cached", e)
        try:
            self._positions = self._broker.get_positions()
        except Exception as e:
            import logging
            logging.getLogger("core.portfolio").warning("get_positions() failed: %s — using cached", e)
        if self._initial_value is None and self._equity:
            self._initial_value = self._equity

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def buying_power(self) -> float:
        return self._buying_power

    @property
    def positions(self) -> Dict[str, dict]:
        return dict(self._positions)

    @property
    def total_unrealized_pl(self) -> float:
        return sum(p["unrealized_pl"] for p in self._positions.values())

    @property
    def total_pnl(self) -> float:
        if self._initial_value:
            return self._equity - self._initial_value
        return 0.0

    @property
    def total_pnl_pct(self) -> float:
        if self._initial_value and self._initial_value > 0:
            return self.total_pnl / self._initial_value
        return 0.0

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def position_side(self, symbol: str) -> Optional[str]:
        p = self._positions.get(symbol)
        return p["side"] if p else None

    def position_qty(self, symbol: str) -> float:
        p = self._positions.get(symbol)
        return p["qty"] if p else 0.0

    def print_summary(self) -> None:
        log.info("─── Portfolio Summary ───────────────────────────────")
        log.info("  Equity:       $%.2f", self._equity)
        log.info("  Cash:         $%.2f", self._cash)
        log.info("  Unrealized:   $%.2f", self.total_unrealized_pl)
        log.info("  Total P&L:    $%.2f (%.2f%%)", self.total_pnl, self.total_pnl_pct * 100)
        log.info("  Positions:    %d", len(self._positions))
        for sym, p in self._positions.items():
            log.info(
                "    %-12s  %s %6.2f  entry=$%.2f  cur=$%.2f  P&L=$%.2f",
                sym, p["side"], p["qty"], p["avg_entry"],
                p["current_price"], p["unrealized_pl"],
            )
        log.info("────────────────────────────────────────────────────")
