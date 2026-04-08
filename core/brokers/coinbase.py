"""Coinbase Advanced Trade broker adapter — HMAC SHA256 auth."""
from __future__ import annotations
import hashlib
import hmac
import time
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["CoinbaseBroker"]

_BASE = "https://api.coinbase.com/api/v3/brokerage"
_TIMEOUT = 10


class CoinbaseBroker(BrokerBase):
    BROKER_NAME = "coinbase"
    SUPPORTED_ASSETS = ["crypto"]

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict:
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self._api_key,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        full_path = "/api/v3/brokerage" + path
        headers = self._sign_headers("GET", full_path)
        r = requests.get(f"{_BASE}{path}", headers=headers,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
        import json as _json
        full_path = "/api/v3/brokerage" + path
        body = _json.dumps(payload)
        headers = self._sign_headers("POST", full_path, body)
        r = requests.post(f"{_BASE}{path}", headers=headers,
                          data=body, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        full_path = "/api/v3/brokerage" + path
        headers = self._sign_headers("DELETE", full_path)
        r = requests.delete(f"{_BASE}{path}", headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get("/accounts")
        accounts = data.get("accounts", [])
        total_usd = sum(
            float(a.get("available_balance", {}).get("value", 0))
            for a in accounts
            if a.get("available_balance", {}).get("currency") == "USD"
        )
        return {
            "equity": total_usd,
            "cash": total_usd,
            "buying_power": total_usd,
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._get("/accounts")
        accounts = data.get("accounts", [])
        result = []
        for a in accounts:
            bal = a.get("available_balance", {})
            qty = float(bal.get("value", 0))
            currency = bal.get("currency", "")
            if qty > 0 and currency != "USD":
                result.append({
                    "symbol": f"{currency}-USD",
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
        time_in_force: str = "gtc",
    ) -> dict:
        import uuid
        payload: dict = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": symbol,
            "side": side.upper(),
        }
        if order_type == "market":
            payload["order_configuration"] = {
                "market_market_ioc": {"base_size": str(qty)}
            }
        else:
            payload["order_configuration"] = {
                "limit_limit_gtc": {
                    "base_size": str(qty),
                    "limit_price": str(limit_price),
                }
            }
        resp = self._post("/orders", payload)
        order = resp.get("success_response", {})
        return {
            "order_id": order.get("order_id", ""),
            "status": "submitted",
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._post("/orders/batch_cancel", {"order_ids": [order_id]})
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        params = {}
        if status != "all":
            params["order_status"] = status.upper()
        data = self._get("/orders/historical/batch", params=params)
        orders = data.get("orders", [])
        return [
            {
                "order_id": o.get("order_id"),
                "symbol": o.get("product_id"),
                "qty": float(o.get("filled_size") or 0),
                "side": o.get("side", "").lower(),
                "status": o.get("status", "").lower(),
                "order_type": o.get("order_type", "").lower(),
            }
            for o in orders
        ]

    def cancel_all_orders(self) -> bool:
        orders = self.get_orders(status="open")
        if not orders:
            return True
        ids = [o["order_id"] for o in orders]
        try:
            self._post("/orders/batch_cancel", {"order_ids": ids})
            return True
        except requests.HTTPError:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get(f"/products/{symbol}")
        return {
            "symbol": symbol,
            "tradable": not data.get("is_disabled", False),
            "marginable": False,
            "shortable": False,
            "asset_class": "crypto",
        }
