"""tastytrade broker adapter — session-based auth."""
from __future__ import annotations
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["TastytradeBroker"]

_BASE = "https://api.tastyworks.com"
_TIMEOUT = 10


class TastytradeBroker(BrokerBase):
    BROKER_NAME = "tastytrade"
    SUPPORTED_ASSETS = ["stocks", "options"]

    def __init__(self, username: str, password: str) -> None:
        # Authenticate and obtain session token
        resp = requests.post(
            f"{_BASE}/sessions",
            json={"login": username, "password": password},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token: str = body["data"]["session-token"]
        self._headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }
        # Fetch default account number
        accts = requests.get(f"{_BASE}/customers/me/accounts", headers=self._headers,
                             timeout=_TIMEOUT)
        accts.raise_for_status()
        items = accts.json().get("data", {}).get("items", [])
        self._account_number: str = items[0]["account"]["account-number"] if items else ""

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{_BASE}{path}", headers=self._headers,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict | None = None) -> dict:
        r = requests.post(f"{_BASE}{path}", headers=self._headers,
                          json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = requests.delete(f"{_BASE}{path}", headers=self._headers, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get(f"/accounts/{self._account_number}/balances")
        bal = data.get("data", {})
        return {
            "equity": float(bal.get("equity", 0)),
            "cash": float(bal.get("cash-balance", 0)),
            "buying_power": float(bal.get("buying-power", 0)),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._get(f"/accounts/{self._account_number}/positions")
        items = data.get("data", {}).get("items", [])
        result = []
        for p in items:
            result.append({
                "symbol": p.get("symbol"),
                "qty": float(p.get("quantity", 0)),
                "side": "long" if float(p.get("quantity-direction", 0) or 0) >= 0 else "short",
                "avg_price": float(p.get("average-open-price", 0) or 0),
                "market_value": float(p.get("market-value", 0) or 0),
                "unrealized_pnl": float(p.get("unrealized-day-gain", 0) or 0),
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
        time_in_force: str = "GTC",
    ) -> dict:
        leg = {
            "instrument-type": "Equity",
            "symbol": symbol,
            "quantity": int(qty),
            "action": "Buy to Open" if side == "buy" else "Sell to Close",
        }
        payload: dict = {
            "order-type": order_type.capitalize(),
            "time-in-force": time_in_force.upper(),
            "legs": [leg],
        }
        if limit_price is not None:
            payload["price"] = str(round(limit_price, 2))
        resp = self._post(f"/accounts/{self._account_number}/orders", payload)
        order = resp.get("data", {}).get("order", {})
        return {
            "order_id": str(order.get("id", "")),
            "status": order.get("status", ""),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._delete(f"/accounts/{self._account_number}/orders/{order_id}")
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._get(f"/accounts/{self._account_number}/orders/live")
        items = data.get("data", {}).get("items", [])
        return [
            {
                "order_id": str(o.get("id")),
                "symbol": (o.get("legs") or [{}])[0].get("symbol", ""),
                "qty": float((o.get("legs") or [{}])[0].get("quantity", 0)),
                "side": (o.get("legs") or [{}])[0].get("action", ""),
                "status": o.get("status"),
                "order_type": o.get("order-type"),
            }
            for o in items
        ]

    def cancel_all_orders(self) -> bool:
        orders = self.get_orders()
        success = True
        for o in orders:
            if not self.cancel_order(o["order_id"]):
                success = False
        return success

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get(f"/instruments/equities/{symbol}")
        instr = data.get("data", {})
        return {
            "symbol": symbol,
            "tradable": instr.get("is-tradeable", True),
            "marginable": instr.get("is-marginable", True),
            "shortable": True,
            "asset_class": "equity",
        }
