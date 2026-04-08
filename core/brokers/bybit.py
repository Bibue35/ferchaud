"""Bybit V5 broker adapter — HMAC SHA256 auth."""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["BybitBroker"]

_BASE = "https://api.bybit.com"
_TIMEOUT = 10
_RECV_WINDOW = "5000"


class BybitBroker(BrokerBase):
    BROKER_NAME = "bybit"
    SUPPORTED_ASSETS = ["crypto"]

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    def _sign(self, params: str, timestamp: str) -> str:
        payload = timestamp + self._api_key + _RECV_WINDOW + params
        return hmac.new(self._api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def _headers(self, params: str) -> dict:
        ts = str(int(time.time() * 1000))
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": self._sign(params, ts),
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        from urllib.parse import urlencode
        qs = urlencode(params or {})
        r = requests.get(f"{_BASE}{path}", headers=self._headers(qs),
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        body = _json.dumps(payload)
        r = requests.post(f"{_BASE}{path}", headers=self._headers(body),
                          data=body, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        accounts = data.get("result", {}).get("list", [])
        acct = accounts[0] if accounts else {}
        equity = float(acct.get("totalEquity", 0) or 0)
        cash = float(acct.get("totalAvailableBalance", 0) or 0)
        return {
            "equity": equity,
            "cash": cash,
            "buying_power": cash,
            "currency": "USDT",
        }

    def get_positions(self) -> list[dict]:
        data = self._get("/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        items = data.get("result", {}).get("list", [])
        result = []
        for p in items:
            qty = float(p.get("size", 0) or 0)
            if qty <= 0:
                continue
            result.append({
                "symbol": p.get("symbol"),
                "qty": qty,
                "side": p.get("side", "").lower(),
                "avg_price": float(p.get("avgPrice", 0) or 0),
                "market_value": float(p.get("positionValue", 0) or 0),
                "unrealized_pnl": float(p.get("unrealisedPnl", 0) or 0),
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
            "category": "linear",
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(qty),
            "timeInForce": time_in_force,
        }
        if order_type.lower() == "limit" and limit_price:
            payload["price"] = str(limit_price)
        resp = self._post("/v5/order/create", payload)
        result = resp.get("result", {})
        return {
            "order_id": result.get("orderId", ""),
            "status": resp.get("retMsg", ""),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._post("/v5/order/cancel", {"category": "linear", "orderId": order_id})
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        path = "/v5/order/realtime" if status == "open" else "/v5/order/history"
        data = self._get(path, {"category": "linear"})
        items = data.get("result", {}).get("list", [])
        return [
            {
                "order_id": o.get("orderId"),
                "symbol": o.get("symbol"),
                "qty": float(o.get("qty", 0) or 0),
                "side": o.get("side", "").lower(),
                "status": o.get("orderStatus", "").lower(),
                "order_type": o.get("orderType", "").lower(),
            }
            for o in items
        ]

    def cancel_all_orders(self) -> bool:
        try:
            self._post("/v5/order/cancel-all", {"category": "linear"})
            return True
        except requests.HTTPError:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get("/v5/market/instruments-info",
                         {"category": "linear", "symbol": symbol})
        items = data.get("result", {}).get("list", [])
        info = items[0] if items else {}
        return {
            "symbol": symbol,
            "tradable": info.get("status") == "Trading",
            "marginable": True,
            "shortable": True,
            "asset_class": "crypto",
        }
