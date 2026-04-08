"""Binance REST broker adapter — HMAC SHA256 auth."""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode
import requests
from core.brokers.base import BrokerBase

__all__ = ["BinanceBroker"]

_BASE = "https://api.binance.com"
_TIMEOUT = 10


class BinanceBroker(BrokerBase):
    BROKER_NAME = "binance"
    SUPPORTED_ASSETS = ["crypto"]

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._headers = {"X-MBX-APIKEY": api_key}

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _get(self, path: str, params: dict | None = None, signed: bool = True) -> dict | list:
        p = self._sign(params or {}) if signed else (params or {})
        r = requests.get(f"{_BASE}{path}", headers=self._headers,
                         params=p, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, params: dict | None = None) -> dict:
        p = self._sign(params or {})
        r = requests.post(f"{_BASE}{path}", headers=self._headers,
                          params=p, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str, params: dict | None = None) -> dict:
        p = self._sign(params or {})
        r = requests.delete(f"{_BASE}{path}", headers=self._headers,
                            params=p, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get("/api/v3/account")
        balances = data.get("balances", [])
        usdt = next((b for b in balances if b["asset"] == "USDT"), {})
        cash = float(usdt.get("free", 0))
        return {
            "equity": cash,
            "cash": cash,
            "buying_power": cash,
            "currency": "USDT",
        }

    def get_positions(self) -> list[dict]:
        data = self._get("/api/v3/account")
        balances = data.get("balances", [])
        result = []
        for b in balances:
            qty = float(b.get("free", 0)) + float(b.get("locked", 0))
            asset = b.get("asset", "")
            if qty > 0 and asset not in ("USDT", "USD"):
                result.append({
                    "symbol": f"{asset}USDT",
                    "qty": qty,
                    "side": "long",
                    "avg_price": 0.0,
                    "market_value": 0.0,
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
        time_in_force: str = "GTC",
    ) -> dict:
        params: dict = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": qty,
        }
        if order_type.upper() == "LIMIT":
            params["price"] = round(limit_price, 8) if limit_price else 0
            params["timeInForce"] = time_in_force.upper()
        resp = self._post("/api/v3/order", params)
        return {
            "order_id": str(resp.get("orderId", "")),
            "status": resp.get("status", "").lower(),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        # Requires symbol — stored in order; simplified: scan open orders
        try:
            orders = self.get_orders(status="open")
            for o in orders:
                if str(o["order_id"]) == str(order_id):
                    self._delete("/api/v3/order", {"symbol": o["symbol"], "orderId": order_id})
                    return True
            return False
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        if status == "open":
            data = self._get("/api/v3/openOrders")
        else:
            data = self._get("/api/v3/allOrders", {"limit": 50})
        orders = data if isinstance(data, list) else []
        return [
            {
                "order_id": str(o.get("orderId")),
                "symbol": o.get("symbol"),
                "qty": float(o.get("origQty", 0)),
                "side": o.get("side", "").lower(),
                "status": o.get("status", "").lower(),
                "order_type": o.get("type", "").lower(),
            }
            for o in orders
        ]

    def cancel_all_orders(self) -> bool:
        try:
            data = self._get("/api/v3/openOrders")
            orders = data if isinstance(data, list) else []
            # Group by symbol and cancel
            symbols = {o["symbol"] for o in orders}
            for sym in symbols:
                self._delete("/api/v3/openOrders", {"symbol": sym})
            return True
        except requests.HTTPError:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get("/api/v3/exchangeInfo", {"symbol": symbol}, signed=False)
        symbols = data.get("symbols", [])
        info = next((s for s in symbols if s["symbol"] == symbol), {})
        return {
            "symbol": symbol,
            "tradable": info.get("status") == "TRADING",
            "marginable": False,
            "shortable": False,
            "asset_class": "crypto",
        }
