"""Robinhood broker adapter — uses robin_stocks library."""
from __future__ import annotations
from typing import Optional
from core.brokers.base import BrokerBase

__all__ = ["RobinhoodBroker"]


class RobinhoodBroker(BrokerBase):
    BROKER_NAME = "robinhood"
    SUPPORTED_ASSETS = ["stocks", "options"]

    def __init__(self, username: str, password: str, mfa_code: str = "") -> None:
        try:
            import robin_stocks.robinhood as rh
            self._rh = rh
        except ImportError:
            raise ImportError("robin_stocks is required: pip install robin_stocks")
        kwargs: dict = {"username": username, "password": password, "store_session": True}
        if mfa_code:
            kwargs["mfa_code"] = mfa_code
        self._rh.login(**kwargs)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        profile = self._rh.load_portfolio_profile()
        return {
            "equity": float(profile.get("equity", 0) or 0),
            "cash": float(self._rh.load_account_profile().get("cash", 0) or 0),
            "buying_power": float(profile.get("withdrawable_amount", 0) or 0),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        positions = self._rh.get_open_stock_positions()
        result = []
        for p in positions:
            qty = float(p.get("quantity", 0) or 0)
            if qty <= 0:
                continue
            result.append({
                "symbol": self._rh.get_instrument_by_url(p["instrument"]).get("symbol", ""),
                "qty": qty,
                "side": "long",
                "avg_price": float(p.get("average_buy_price", 0) or 0),
                "market_value": qty * float(p.get("average_buy_price", 0) or 0),
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
        if side.lower() == "buy":
            if order_type == "market":
                resp = self._rh.order_buy_market(symbol, int(qty), timeInForce=time_in_force)
            else:
                resp = self._rh.order_buy_limit(symbol, int(qty), limit_price, timeInForce=time_in_force)
        else:
            if order_type == "market":
                resp = self._rh.order_sell_market(symbol, int(qty), timeInForce=time_in_force)
            else:
                resp = self._rh.order_sell_limit(symbol, int(qty), limit_price, timeInForce=time_in_force)
        resp = resp or {}
        return {
            "order_id": resp.get("id", ""),
            "status": resp.get("state", "submitted"),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._rh.cancel_stock_order(order_id)
            return True
        except Exception:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        if status == "open":
            orders = self._rh.get_all_open_stock_orders()
        else:
            orders = self._rh.get_all_stock_orders()
        result = []
        for o in (orders or []):
            result.append({
                "order_id": o.get("id"),
                "symbol": o.get("symbol", ""),
                "qty": float(o.get("quantity", 0) or 0),
                "side": o.get("side"),
                "status": o.get("state"),
                "order_type": o.get("type"),
            })
        return result

    def cancel_all_orders(self) -> bool:
        try:
            self._rh.cancel_all_stock_orders()
            return True
        except Exception:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        info = self._rh.get_instruments_by_symbols(symbol)
        data = info[0] if info else {}
        return {
            "symbol": symbol,
            "tradable": data.get("tradeable", False),
            "marginable": data.get("margin_initial_ratio") is not None,
            "shortable": data.get("tradeable", False),
            "asset_class": data.get("type", "equity"),
        }
