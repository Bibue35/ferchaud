"""Tradier broker adapter — REST API via Bearer token."""
from __future__ import annotations
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["TradierBroker"]

_LIVE_URL = "https://api.tradier.com/v1"
_SANDBOX_URL = "https://sandbox.tradier.com/v1"
_TIMEOUT = 10


class TradierBroker(BrokerBase):
    BROKER_NAME = "tradier"
    SUPPORTED_ASSETS = ["stocks", "options", "futures"]

    def __init__(self, api_key: str, account_id: str, sandbox: bool = False) -> None:
        self._account_id = account_id
        self._base = _SANDBOX_URL if sandbox else _LIVE_URL
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{self._base}{path}", headers=self._headers,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict | None = None) -> dict:
        r = requests.post(f"{self._base}{path}", headers=self._headers,
                          data=data, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = requests.delete(f"{self._base}{path}", headers=self._headers,
                            timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get(f"/accounts/{self._account_id}/balances")
        bal = data.get("balances", {})
        return {
            "equity": float(bal.get("total_equity", 0)),
            "cash": float(bal.get("cash", {}).get("cash_available", 0)),
            "buying_power": float(bal.get("margin", {}).get("stock_buying_power", 0)),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._get(f"/accounts/{self._account_id}/positions")
        positions = data.get("positions", {}).get("position", [])
        if isinstance(positions, dict):
            positions = [positions]
        result = []
        for p in positions:
            result.append({
                "symbol": p.get("symbol"),
                "qty": float(p.get("quantity", 0)),
                "side": "long" if float(p.get("quantity", 0)) > 0 else "short",
                "avg_price": float(p.get("cost_basis", 0)) / max(abs(float(p.get("quantity", 1))), 1),
                "market_value": 0.0,  # Not returned in positions endpoint
                "unrealized_pnl": 0.0,
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
        data: dict = {
            "class": "equity",
            "symbol": symbol,
            "side": side,
            "quantity": str(int(qty)),
            "type": order_type,
            "duration": time_in_force,
        }
        if limit_price is not None:
            data["price"] = str(round(limit_price, 2))
        resp = self._post(f"/accounts/{self._account_id}/orders", data=data)
        order = resp.get("order", {})
        return {
            "order_id": str(order.get("id", "")),
            "status": order.get("status", ""),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._delete(f"/accounts/{self._account_id}/orders/{order_id}")
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._get(f"/accounts/{self._account_id}/orders")
        orders = data.get("orders", {}).get("order", [])
        if isinstance(orders, dict):
            orders = [orders]
        return [
            {
                "order_id": str(o.get("id")),
                "symbol": o.get("symbol"),
                "qty": float(o.get("quantity", 0)),
                "side": o.get("side"),
                "status": o.get("status"),
                "order_type": o.get("type"),
            }
            for o in orders
            if status == "all" or o.get("status") == status
        ]

    def cancel_all_orders(self) -> bool:
        orders = self.get_orders(status="open")
        success = True
        for o in orders:
            if not self.cancel_order(o["order_id"]):
                success = False
        return success

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get("/markets/quotes", params={"symbols": symbol})
        quote = data.get("quotes", {}).get("quote", {})
        return {
            "symbol": symbol,
            "tradable": quote.get("type") is not None,
            "marginable": True,
            "shortable": True,
            "asset_class": quote.get("type", "equity"),
        }
