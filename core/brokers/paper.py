"""Paper broker — in-memory order simulation, no real API calls."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from core.brokers.base import BrokerBase

__all__ = ["PaperBroker"]


class PaperBroker(BrokerBase):
    BROKER_NAME = "paper"
    SUPPORTED_ASSETS = ["stocks", "options", "futures", "crypto"]

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._cash = starting_cash
        self._positions: dict[str, dict] = {}  # symbol -> position dict
        self._orders: dict[str, dict] = {}     # order_id -> order dict

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        market_value = sum(
            p["qty"] * p["avg_price"] for p in self._positions.values()
        )
        equity = self._cash + market_value
        return {
            "equity": round(equity, 2),
            "cash": round(self._cash, 2),
            "buying_power": round(self._cash, 2),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        result = []
        for symbol, p in self._positions.items():
            if p["qty"] == 0:
                continue
            result.append({
                "symbol": symbol,
                "qty": p["qty"],
                "side": "long" if p["qty"] > 0 else "short",
                "avg_price": p["avg_price"],
                "market_value": p["qty"] * p["avg_price"],
                "unrealized_pnl": 0.0,  # No live price feed in paper mode
            })
        return result

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        time_in_force: str = "gtc",
    ) -> dict:
        order_id = str(uuid.uuid4())
        fill_price = limit_price or 0.0  # Market orders fill at 0 w/o price feed
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "limit_price": limit_price,
            "time_in_force": time_in_force,
            "status": "filled",  # Paper: instant fill
            "filled_at": datetime.utcnow().isoformat(),
            "fill_price": fill_price,
        }
        # Simulate fill: update position
        if order_type == "market" or limit_price is not None:
            self._fill_order(symbol, qty, side, fill_price)
            order["status"] = "filled"
        self._orders[order_id] = order
        return {
            "order_id": order_id,
            "status": order["status"],
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def _fill_order(self, symbol: str, qty: float, side: str, price: float) -> None:
        cost = qty * price
        if side.lower() == "buy":
            self._cash -= cost
            pos = self._positions.get(symbol, {"qty": 0, "avg_price": 0.0})
            total_qty = pos["qty"] + qty
            if total_qty > 0:
                pos["avg_price"] = (pos["qty"] * pos["avg_price"] + cost) / total_qty
            pos["qty"] = total_qty
            self._positions[symbol] = pos
        else:  # sell
            self._cash += cost
            pos = self._positions.get(symbol, {"qty": 0, "avg_price": 0.0})
            pos["qty"] -= qty
            if pos["qty"] <= 0:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = pos

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order["status"] == "open":
            order["status"] = "cancelled"
            return True
        return False

    def get_orders(self, status: str = "open") -> list[dict]:
        return [
            o for o in self._orders.values()
            if status == "all" or o["status"] == status
        ]

    def cancel_all_orders(self) -> bool:
        for order in self._orders.values():
            if order["status"] == "open":
                order["status"] = "cancelled"
        return True

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "tradable": True,
            "marginable": True,
            "shortable": True,
            "asset_class": "paper",
        }
