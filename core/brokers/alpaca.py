"""Alpaca broker adapter — wraps the existing core.broker.Broker class."""
from __future__ import annotations
from typing import Optional

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from config import CONFIG
from utils.logger import get_logger
from core.brokers.base import BrokerBase

__all__ = ["AlpacaBroker"]

log = get_logger("core.brokers.alpaca")


class AlpacaBroker(BrokerBase):
    BROKER_NAME = "alpaca"
    SUPPORTED_ASSETS = ["stocks", "options", "etfs"]

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        base_url: str = "",
        paper: bool = False,
    ) -> None:
        _key = api_key or CONFIG.api_key
        _secret = secret_key or CONFIG.secret_key
        _url = base_url or CONFIG.base_url
        self._api = tradeapi.REST(_key, _secret, _url, api_version="v2")
        # Enforce 10-second timeout on every HTTP call
        try:
            _orig = self._api._session.request

            def _timed(*a, **kw):
                kw.setdefault("timeout", 10)
                return _orig(*a, **kw)

            self._api._session.request = _timed
        except Exception:
            pass
        log.info("AlpacaBroker connected — url=%s", _url)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        a = self._api.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        result = []
        for p in self._api.list_positions():
            result.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side,
                "avg_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
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
        try:
            kwargs: dict = dict(
                symbol=symbol, qty=qty, side=side,
                type=order_type, time_in_force=time_in_force,
            )
            if limit_price is not None:
                kwargs["limit_price"] = round(limit_price, 2)
            o = self._api.submit_order(**kwargs)
            return {"order_id": o.id, "status": o.status, "symbol": o.symbol,
                    "qty": float(o.qty), "side": o.side}
        except APIError as e:
            log.error("place_order error: %s", e)
            raise

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._api.cancel_order(order_id)
            return True
        except APIError as e:
            log.error("cancel_order(%s): %s", order_id, e)
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        orders = []
        for o in self._api.list_orders(status=status):
            orders.append({
                "order_id": o.id, "symbol": o.symbol,
                "qty": float(o.qty or 0), "side": o.side,
                "status": o.status, "order_type": o.type,
            })
        return orders

    def cancel_all_orders(self) -> bool:
        try:
            self._api.cancel_all_orders()
            return True
        except APIError as e:
            log.error("cancel_all_orders: %s", e)
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        try:
            a = self._api.get_asset(symbol)
            return {
                "symbol": a.symbol,
                "tradable": a.tradable,
                "marginable": getattr(a, "marginable", False),
                "shortable": getattr(a, "shortable", False),
                "asset_class": getattr(a, "asset_class", "us_equity"),
            }
        except APIError as e:
            log.error("get_asset(%s): %s", symbol, e)
            raise
