"""Kraken REST broker adapter — HMAC SHA512 auth."""
from __future__ import annotations
import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["KrakenBroker"]

_BASE = "https://api.kraken.com"
_TIMEOUT = 10


class KrakenBroker(BrokerBase):
    BROKER_NAME = "kraken"
    SUPPORTED_ASSETS = ["crypto"]

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = base64.b64decode(api_secret)

    def _sign(self, url_path: str, data: dict) -> str:
        post_data = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + post_data).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(self._api_secret, message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()

    def _private(self, endpoint: str, params: dict | None = None) -> dict:
        path = f"/0/private/{endpoint}"
        data = params or {}
        data["nonce"] = str(int(time.time() * 1000))
        headers = {
            "API-Key": self._api_key,
            "API-Sign": self._sign(path, data),
        }
        r = requests.post(f"{_BASE}{path}", headers=headers,
                          data=data, timeout=_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"Kraken error: {body['error']}")
        return body.get("result", {})

    def _public(self, endpoint: str, params: dict | None = None) -> dict:
        r = requests.get(f"{_BASE}/0/public/{endpoint}", params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json().get("result", {})

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        bal = self._private("Balance")
        zusd = float(bal.get("ZUSD", 0))
        return {
            "equity": zusd,
            "cash": zusd,
            "buying_power": zusd,
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._private("OpenPositions")
        result = []
        for pos_id, p in data.items():
            result.append({
                "symbol": p.get("pair"),
                "qty": float(p.get("vol", 0)),
                "side": "long" if p.get("type") == "buy" else "short",
                "avg_price": float(p.get("cost", 0)) / max(float(p.get("vol", 1)), 0.001),
                "market_value": float(p.get("value", 0)),
                "unrealized_pnl": float(p.get("net", 0)),
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
            "pair": symbol,
            "type": side,
            "ordertype": order_type,
            "volume": str(qty),
        }
        if order_type == "limit" and limit_price:
            params["price"] = str(limit_price)
        resp = self._private("AddOrder", params)
        txids = resp.get("txid", [])
        return {
            "order_id": txids[0] if txids else "",
            "status": "submitted",
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._private("CancelOrder", {"txid": order_id})
            return True
        except Exception:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._private("OpenOrders")
        orders = data.get("open", {})
        return [
            {
                "order_id": oid,
                "symbol": o.get("descr", {}).get("pair"),
                "qty": float(o.get("vol", 0)),
                "side": o.get("descr", {}).get("type"),
                "status": o.get("status"),
                "order_type": o.get("descr", {}).get("ordertype"),
            }
            for oid, o in orders.items()
        ]

    def cancel_all_orders(self) -> bool:
        try:
            self._private("CancelAll")
            return True
        except Exception:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._public("AssetPairs", {"pair": symbol})
        info = data.get(symbol, {})
        return {
            "symbol": symbol,
            "tradable": bool(info),
            "marginable": info.get("margin_call", 0) > 0,
            "shortable": info.get("margin_call", 0) > 0,
            "asset_class": "crypto",
        }
