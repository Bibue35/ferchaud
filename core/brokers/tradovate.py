"""Tradovate futures broker adapter — REST API with JWT auth."""
from __future__ import annotations
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["TradovateBroker"]

_LIVE_URL = "https://live.tradovateapi.com/v1"
_DEMO_URL = "https://demo.tradovateapi.com/v1"
_TIMEOUT = 10


class TradovateBroker(BrokerBase):
    BROKER_NAME = "tradovate"
    SUPPORTED_ASSETS = ["futures"]

    def __init__(
        self,
        username: str,
        password: str,
        app_id: str,
        app_version: str,
        demo: bool = False,
    ) -> None:
        self._base = _DEMO_URL if demo else _LIVE_URL
        # Authenticate and get access token
        resp = requests.post(
            f"{self._base}/auth/accesstokenrequest",
            json={
                "name": username,
                "password": password,
                "appId": app_id,
                "appVersion": app_version,
                "cid": 0,
                "sec": "",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token: str = body.get("accessToken", "")
        self._account_id: int = body.get("userId", 0)
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        # Fetch first account
        accts = self._get("/account/list")
        if isinstance(accts, list) and accts:
            self._account_id = accts[0].get("id", self._account_id)

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        r = requests.get(f"{self._base}{path}", headers=self._headers,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{self._base}{path}", headers=self._headers,
                          json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get(f"/cashbalance/getcashbalancesnapshot",
                         params={"accountId": self._account_id})
        snap = data if isinstance(data, dict) else {}
        equity = float(snap.get("totalCashValue", 0) or 0)
        return {
            "equity": equity,
            "cash": equity,
            "buying_power": float(snap.get("purchasingPower", 0) or 0),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._get("/position/list", params={"accountId": self._account_id})
        positions = data if isinstance(data, list) else []
        result = []
        for p in positions:
            qty = float(p.get("netPos", 0) or 0)
            if qty == 0:
                continue
            result.append({
                "symbol": str(p.get("contractId", "")),
                "qty": abs(qty),
                "side": "long" if qty > 0 else "short",
                "avg_price": float(p.get("avgPrice", 0) or 0),
                "market_value": 0.0,
                "unrealized_pnl": float(p.get("openPnL", 0) or 0),
            })
        return result

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "Market",
        limit_price: Optional[float] = None,
        time_in_force: str = "GTC",
    ) -> dict:
        payload: dict = {
            "accountSpec": "",
            "accountId": self._account_id,
            "action": "Buy" if side.lower() == "buy" else "Sell",
            "symbol": symbol,
            "orderQty": int(qty),
            "orderType": order_type,
            "timeInForce": time_in_force,
            "isAutomated": True,
        }
        if order_type.lower() == "limit" and limit_price:
            payload["price"] = limit_price
        resp = self._post("/order/placeorder", payload)
        order = resp.get("ordStatus", {})
        return {
            "order_id": str(resp.get("orderId", "")),
            "status": order.get("name", "submitted"),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._post("/order/cancelorder", {"orderId": int(order_id)})
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._get("/order/list", params={"accountId": self._account_id})
        orders = data if isinstance(data, list) else []
        return [
            {
                "order_id": str(o.get("id")),
                "symbol": str(o.get("contractId", "")),
                "qty": float(o.get("orderQty", 0) or 0),
                "side": o.get("action", "").lower(),
                "status": o.get("ordStatus", {}).get("name", "").lower() if isinstance(o.get("ordStatus"), dict) else "",
                "order_type": o.get("orderType", "").lower(),
            }
            for o in orders
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
        data = self._get("/contract/find", params={"name": symbol})
        info = data if isinstance(data, dict) else {}
        return {
            "symbol": symbol,
            "tradable": bool(info.get("id")),
            "marginable": True,
            "shortable": True,
            "asset_class": "futures",
        }
