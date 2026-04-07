"""Alpaca broker wrapper — account info, positions, orders."""
from typing import Dict, List, Optional

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from config import CONFIG
from utils.logger import get_logger

log = get_logger("core.broker")


class Broker:
    def __init__(self) -> None:
        self._api = tradeapi.REST(
            CONFIG.api_key,
            CONFIG.secret_key,
            CONFIG.base_url,
            api_version="v2",
        )
        # Patch requests session to enforce a 25-second timeout on all HTTP calls
        try:
            _orig = self._api._session.request
            def _timed_request(*a, **kw):
                if not kw.get("timeout"):  # Override None or missing
                    kw["timeout"] = 10
                return _orig(*a, **kw)
            self._api._session.request = _timed_request
        except Exception:
            pass

        log.info(
            "Broker connected — mode=%s  url=%s",
            CONFIG.trading_mode.upper(),
            CONFIG.base_url,
        )

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        a = self._api.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "last_equity": float(a.last_equity),
            "daytrade_count": int(a.daytrade_count),
            "pattern_day_trader": a.pattern_day_trader,
            "trading_blocked": a.trading_blocked,
        }

    def is_market_open(self) -> bool:
        try:
            clock = self._api.get_clock()
            return clock.is_open
        except Exception:
            return False

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> Dict[str, dict]:
        positions = {}
        for p in self._api.list_positions():
            positions[p.symbol] = {
                "qty": float(p.qty),
                "side": p.side,
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
            }
        return positions

    def get_position(self, symbol: str) -> Optional[dict]:
        try:
            p = self._api.get_position(symbol)
            return {
                "qty": float(p.qty),
                "side": p.side,
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "current_price": float(p.current_price),
            }
        except APIError:
            return None

    def close_position(self, symbol: str) -> Optional[dict]:
        try:
            order = self._api.close_position(symbol)
            log.info("Closed position: %s", symbol)
            return {"id": order.id, "status": order.status}
        except APIError as e:
            log.error("close_position(%s): %s", symbol, e)
            return None

    def close_all_positions(self) -> None:
        self._api.close_all_positions()
        log.warning("ALL positions closed")

    # ── Orders ────────────────────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "gtc",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        try:
            kwargs = dict(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force=time_in_force,
            )
            if limit_price is not None:
                kwargs["limit_price"] = round(limit_price, 2)
            if stop_price is not None:
                kwargs["stop_price"] = round(stop_price, 2)
            if client_order_id:
                kwargs["client_order_id"] = client_order_id

            order = self._api.submit_order(**kwargs)
            log.info(
                "Order submitted: %s %s %s @ %s [%s]",
                side.upper(), qty, symbol, order_type, order.id,
            )
            return {
                "id": order.id,
                "symbol": order.symbol,
                "qty": float(order.qty),
                "side": order.side,
                "type": order.type,
                "status": order.status,
            }
        except APIError as e:
            log.error("submit_order(%s %s %s): %s", side, qty, symbol, e)
            return None

    def submit_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Submit a bracket order: entry + stop-loss + take-profit as one atomic unit.
        This avoids wash-trade rejections from placing opposing orders separately."""
        try:
            kwargs = dict(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force="gtc",
                order_class="bracket",
            )
            if limit_price is not None:
                kwargs["limit_price"] = round(limit_price, 2)
            if stop_loss_price is not None:
                kwargs["stop_loss"] = {"stop_price": round(stop_loss_price, 2)}
            if take_profit_price is not None:
                kwargs["take_profit"] = {"limit_price": round(take_profit_price, 2)}
            if client_order_id:
                kwargs["client_order_id"] = client_order_id

            order = self._api.submit_order(**kwargs)
            log.info(
                "Bracket order: %s %s %s @ %s  SL=%s  TP=%s [%s]",
                side.upper(), qty, symbol, order_type,
                stop_loss_price, take_profit_price, order.id,
            )
            return {
                "id": order.id,
                "symbol": order.symbol,
                "qty": float(order.qty),
                "side": order.side,
                "type": order.type,
                "status": order.status,
            }
        except APIError as e:
            log.error("submit_bracket(%s %s %s): %s", side, qty, symbol, e)
            return None

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._api.cancel_order(order_id)
            log.info("Cancelled order %s", order_id)
            return True
        except APIError as e:
            log.error("cancel_order(%s): %s", order_id, e)
            return False

    def get_order(self, order_id: str) -> Optional[dict]:
        try:
            o = self._api.get_order(order_id)
            return {
                "id": o.id,
                "symbol": o.symbol,
                "qty": float(o.qty),
                "filled_qty": float(o.filled_qty or 0),
                "side": o.side,
                "type": o.type,
                "status": o.status,
                "filled_avg_price": float(o.filled_avg_price or 0),
            }
        except APIError as e:
            log.error("get_order(%s): %s", order_id, e)
            return None

    def list_positions(self) -> List[dict]:
        """Return positions as a list of dicts (dashboard-friendly)."""
        result = []
        for p in self._api.list_positions():
            result.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side,
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            })
        return result

    def list_open_orders(self) -> List[dict]:
        orders = []
        for o in self._api.list_orders(status="open"):
            orders.append({
                "id": o.id,
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else 0,
                "side": o.side,
                "type": o.type,
                "status": o.status,
                "limit_price": str(o.limit_price) if o.limit_price else "—",
                "stop_price": str(o.stop_price) if o.stop_price else "—",
            })
        return orders
