"""TradeStation REST broker adapter — OAuth2 Bearer token.

NOTE: OAuth2 authorization code flow must be completed in the browser first.
Obtain access_token via: https://api.tradestation.com/v3/authorize
Store token in credentials and refresh using /security/authorize endpoint.
"""
from __future__ import annotations
from typing import Optional
import requests
from core.brokers.base import BrokerBase

__all__ = ["TradeStationBroker"]

_BASE = "https://api.tradestation.com/v3"
_TIMEOUT = 10


class TradeStationBroker(BrokerBase):
    BROKER_NAME = "tradestation"
    SUPPORTED_ASSETS = ["stocks", "options", "futures"]

    def __init__(self, access_token: str, account_id: str = "") -> None:
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._account_id = account_id
        if not account_id:
            self._account_id = self._fetch_default_account()

    def _fetch_default_account(self) -> str:
        try:
            r = requests.get(f"{_BASE}/brokerage/accounts",
                             headers=self._headers, timeout=_TIMEOUT)
            r.raise_for_status()
            accts = r.json().get("Accounts", [])
            return accts[0].get("AccountID", "") if accts else ""
        except Exception:
            return ""

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{_BASE}{path}", headers=self._headers,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict) -> dict:
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
        data = self._get(f"/brokerage/accounts/{self._account_id}/balances")
        bal = data.get("Balances", [{}])[0] if data.get("Balances") else {}
        return {
            "equity": float(bal.get("Equity", 0)),
            "cash": float(bal.get("CashBalance", 0)),
            "buying_power": float(bal.get("BuyingPower", 0)),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        data = self._get(f"/brokerage/accounts/{self._account_id}/positions")
        positions = data.get("Positions", [])
        result = []
        for p in positions:
            result.append({
                "symbol": p.get("Symbol"),
                "qty": float(p.get("Quantity", 0)),
                "side": "long" if float(p.get("Quantity", 0)) > 0 else "short",
                "avg_price": float(p.get("AveragePrice", 0)),
                "market_value": float(p.get("MarketValue", 0)),
                "unrealized_pnl": float(p.get("UnrealizedProfitLoss", 0)),
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
            "AccountID": self._account_id,
            "Symbol": symbol,
            "Quantity": str(int(qty)),
            "OrderType": order_type,
            "TradeAction": "BUY" if side.lower() == "buy" else "SELL",
            "TimeInForce": {"Duration": time_in_force},
            "Route": "Intelligent",
        }
        if order_type.lower() == "limit" and limit_price:
            payload["LimitPrice"] = str(round(limit_price, 2))
        resp = self._post("/orderexecution/orders", payload)
        orders = resp.get("Orders", [{}])
        order = orders[0] if orders else {}
        return {
            "order_id": str(order.get("OrderID", "")),
            "status": order.get("Status", "submitted"),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._delete(f"/orderexecution/orders/{order_id}")
            return True
        except requests.HTTPError:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        data = self._get(f"/brokerage/accounts/{self._account_id}/orders")
        orders = data.get("Orders", [])
        return [
            {
                "order_id": str(o.get("OrderID")),
                "symbol": o.get("Symbol"),
                "qty": float(o.get("Quantity", 0)),
                "side": o.get("BuySell", "").lower(),
                "status": o.get("Status", "").lower(),
                "order_type": o.get("OrderType", "").lower(),
            }
            for o in orders
            if status == "all" or o.get("Status", "").lower() == status
        ]

    def cancel_all_orders(self) -> bool:
        orders = self.get_orders(status="Open")
        success = True
        for o in orders:
            if not self.cancel_order(o["order_id"]):
                success = False
        return success

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        data = self._get(f"/marketdata/symbollookup/{symbol}")
        symbols = data.get("Symbols", [{}])
        info = symbols[0] if symbols else {}
        return {
            "symbol": symbol,
            "tradable": bool(info),
            "marginable": True,
            "shortable": True,
            "asset_class": info.get("AssetType", "").lower(),
        }
