"""Abstract base class for all broker integrations."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional

__all__ = ["BrokerBase"]


class BrokerBase(ABC):
    """Every broker adapter must subclass this and implement all abstract methods."""

    BROKER_NAME: str = ""
    SUPPORTED_ASSETS: list[str] = []

    # ── Account ───────────────────────────────────────────────────────────────

    @abstractmethod
    def get_account(self) -> dict:
        """Return account snapshot.

        Returns:
            dict with keys: equity, cash, buying_power, currency
        """

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return open positions.

        Returns:
            list of dicts with keys: symbol, qty, side, avg_price,
            market_value, unrealized_pnl
        """

    # ── Orders ────────────────────────────────────────────────────────────────

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "gtc",
    ) -> dict:
        """Submit an order.

        Returns:
            dict with keys: order_id, status, symbol, qty, side
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by ID."""

    @abstractmethod
    def get_orders(self, status: str = "open") -> list[dict]:
        """Fetch orders filtered by status (open / closed / all)."""

    @abstractmethod
    def cancel_all_orders(self) -> bool:
        """Cancel every open order."""

    # ── Assets ────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_asset(self, symbol: str) -> dict:
        """Return asset metadata.

        Returns:
            dict with keys: symbol, tradable, marginable, shortable, asset_class
        """
