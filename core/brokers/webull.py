"""Webull broker adapter — uses webull Python library."""
from __future__ import annotations
from typing import Optional
from core.brokers.base import BrokerBase

__all__ = ["WebullBroker"]


class WebullBroker(BrokerBase):
    BROKER_NAME = "webull"
    SUPPORTED_ASSETS = ["stocks", "options"]

    def __init__(self, username: str, password: str, device_id: str = "",
                 mfa_code: str = "", trading_pin: str = "") -> None:
        try:
            from webull import webull
            self._wb = webull()
        except ImportError:
            raise ImportError("webull is required: pip install webull")
        if device_id:
            self._wb._did = device_id
        self._wb.login(username, password, mfa=mfa_code)
        if trading_pin:
            self._wb.get_trade_token(trading_pin)

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        acct = self._wb.get_account()
        net_liquidation = float(acct.get("netLiquidation", 0) or 0)
        cash = float(acct.get("cashBalance", 0) or 0)
        return {
            "equity": net_liquidation,
            "cash": cash,
            "buying_power": float(acct.get("dayTradeLimit", 0) or 0),
            "currency": "USD",
        }

    def get_positions(self) -> list[dict]:
        positions = self._wb.get_positions()
        result = []
        for p in (positions or []):
            qty = float(p.get("position", 0) or 0)
            result.append({
                "symbol": p.get("ticker", {}).get("symbol", ""),
                "qty": qty,
                "side": "long" if qty > 0 else "short",
                "avg_price": float(p.get("costPrice", 0) or 0),
                "market_value": float(p.get("marketValue", 0) or 0),
                "unrealized_pnl": float(p.get("unrealizedProfitLoss", 0) or 0),
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
        action = "BUY" if side.lower() == "buy" else "SELL"
        if order_type.upper() == "MARKET" or order_type.upper() == "MKT":
            resp = self._wb.place_order(stock=symbol, action=action,
                                        orderType="MKT", quant=int(qty),
                                        timeInForce=time_in_force)
        else:
            resp = self._wb.place_order(stock=symbol, action=action,
                                        orderType="LMT", quant=int(qty),
                                        price=limit_price, timeInForce=time_in_force)
        resp = resp or {}
        return {
            "order_id": str(resp.get("orderId", "")),
            "status": resp.get("status", "submitted"),
            "symbol": symbol,
            "qty": qty,
            "side": side,
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._wb.cancel_order(order_id)
            return True
        except Exception:
            return False

    def get_orders(self, status: str = "open") -> list[dict]:
        orders = self._wb.get_current_orders()
        result = []
        for o in (orders or []):
            result.append({
                "order_id": str(o.get("orderId", "")),
                "symbol": o.get("ticker", {}).get("symbol", ""),
                "qty": float(o.get("quantity", 0) or 0),
                "side": o.get("action", "").lower(),
                "status": o.get("status", "").lower(),
                "order_type": o.get("orderType", "").lower(),
            })
        return result

    def cancel_all_orders(self) -> bool:
        try:
            self._wb.cancel_all_orders()
            return True
        except Exception:
            return False

    # ── Assets ────────────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> dict:
        info = self._wb.get_ticker(symbol)
        return {
            "symbol": symbol,
            "tradable": bool(info),
            "marginable": True,
            "shortable": True,
            "asset_class": "equity",
        }
