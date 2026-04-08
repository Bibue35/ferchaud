"""Interactive Brokers Client Portal REST API adapter.

IBKR Client Portal Gateway must be running locally at https://localhost:5000.
Start it with: java -jar clientportal.gw/root/dist/ibgateway.jar root/conf.yaml
"""
from __future__ import annotations
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["IBKRBroker"]

_BASE = "https://localhost:5000/v1/api"
_TIMEOUT = 10


class IBKRBroker(BrokerBase):
    BROKER_NAME = "ibkr"
    SUPPORTED_ASSETS = ["stocks", "options", "futures", "forex"]

    def __init__(self, account_id: str = "", verify_ssl: bool = False) -> None:
        # Client Portal uses a self-signed cert by default
        self._verify = verify_ssl
        self._account_id = account_id
        if not account_id:
            self._account_id = self._fetch_default_account()

    def _fetch_default_account(self) -> str:
        try:
            r = requests.get(f"{_BASE}/portfolio/accounts",
                             verify=self._verify, timeout=_TIMEOUT)
            r.raise_for_status()
            accounts = r.json()
            return accounts[0].get("accountId", "") if accounts else ""
        except Exception:
            return ""

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        r = requests.get(f"{_BASE}{path}", params=params,
                         verify=self._verify, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict | None = None) -> dict:
        r = requests.post(f"{_BASE}{path}", json=payload or {},
                          verify=self._verify, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = requests.delete(f"{_BASE}{path}", verify=self._verify, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get(f"/portfolio/{self._account_id}/summary")
        summary = data if isinstance(data, dict) else {}
        def _val(key: str) -> float:
            return float(summary.get(key, {}).get("amount", 0))
        return {
            "equity": _val("netliquidation"),
            "cash": _val("cashbalance"),
            "buying_power": _val("buyingpower"),
            "currency": summary.get("netliquidation", {}).get("currency", "USD"),
        }

    def get_positions(self) -> list[dict]:
        data = self._get(f"/portfolio/{self._account_id}/positions/0")
        positions = data if isinstance(data, list) else []
        result = []
        for p in positions:
            result.append({
                "symbol": p.get("ticker", p.get("contractDesc", "")),
                "qty": float(p.get("position", 0)),
                "side": "long" if float(p.get("position", 0)) > 0 else "short",
                "avg_price": float(p.get("avgCost", 0)),
                "market_value": float(p.get("mktValue", 0)),
                "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
            })
        return result

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "MKT",
        limit_price: Optional[float] = None,
        time_in_force: str = "GTC",
    ) -> dict:
        payload = {
            "acctId": self._account_id,
            "conid": 0,  # Caller should pass conid in symbol for real use
            "secType": "STK",
            "orderType": order_type.upper(),
            "side": side.upper(),
            "quantity": qty,
            "tif": time_in_force,
            "ticker": symbol,
        }
        if order_type.upper() == "LMT" and limit_price:
            payload["price"] = limit_price
        resp = self._post(f"/iserver/account/{self._account_id}/order", payload)
        orders = resp if isinstance(resp, list) else [resp]
        order = orders[0] if orders else {}
        return {
            "order_id": str(order.get("order_id", "")),
            "status": order.get("order_status", "submitted"),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._delete(f"/iserver/account/{self._account_id}/order/{order_id}")
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._get("/iserver/account/orders")
        orders = data.get("orders", []) if isinstance(data, dict) else []
        return [
            {
                "order_id": str(o.get("orderId")),
                "symbol": o.get("ticker"),
                "qty": float(o.get("remainingQuantity", 0)),
                "side": o.get("side", "").lower(),
                "status": o.get("status", "").lower(),
                "order_type": o.get("orderType", "").lower(),
            }
            for o in orders
            if status == "all" or o.get("status", "").lower() == status
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
        data = self._get("/iserver/secdef/search", params={"symbol": symbol})
        contracts = data if isinstance(data, list) else []
        info = contracts[0] if contracts else {}
        return {
            "symbol": symbol,
            "tradable": bool(info),
            "marginable": True,
            "shortable": True,
            "asset_class": info.get("secType", "STK").lower(),
        }
