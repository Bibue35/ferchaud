"""
Alpaca broker wrapper — account info, positions, orders.

Now with TTL caching for the high-traffic endpoints (account, positions,
open orders, market clock). Strategies hit these repeatedly per cycle;
caching cuts cloud egress and rate-limit pressure dramatically.

Cache TTLs are intentionally short so freshness wins over throughput
on anything that touches order routing.
"""
import time
import threading
from typing import Dict, List, Optional, Any

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

from config import CONFIG
from utils.logger import get_logger

log = get_logger("core.broker")


class _TTLCache:
    """Tiny thread-safe TTL cache. Per-key TTL, in-memory, no eviction."""
    def __init__(self) -> None:
        self._d: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, key: str, ttl: float):
        with self._lock:
            entry = self._d.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.time() - ts > ttl:
                return None
            return val

    def put(self, key: str, val: Any) -> None:
        with self._lock:
            self._d[key] = (time.time(), val)

    def invalidate(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._d.pop(k, None)


class Broker:
    # Cache TTLs (seconds) — small enough that price-sensitive checks stay fresh
    TTL_ACCOUNT = 3.0
    TTL_POSITIONS = 2.0
    TTL_ORDERS = 2.0
    TTL_CLOCK = 30.0

    def __init__(self) -> None:
        self._api = tradeapi.REST(
            CONFIG.api_key,
            CONFIG.secret_key,
            CONFIG.base_url,
            api_version="v2",
        )
        self._cache = _TTLCache()
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

    def invalidate_cache(self) -> None:
        """Force re-fetch on next read. Call after order submission / fill."""
        self._cache.invalidate("account", "positions", "open_orders")

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        cached = self._cache.get("account", self.TTL_ACCOUNT)
        if cached is not None:
            return cached
        a = self._api.get_account()
        out = {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "last_equity": float(a.last_equity),
            "daytrade_count": int(a.daytrade_count),
            "pattern_day_trader": a.pattern_day_trader,
            "trading_blocked": a.trading_blocked,
        }
        self._cache.put("account", out)
        return out

    def is_market_open(self) -> bool:
        cached = self._cache.get("clock", self.TTL_CLOCK)
        if cached is not None:
            return cached
        try:
            clock = self._api.get_clock()
            val = bool(clock.is_open)
            self._cache.put("clock", val)
            return val
        except Exception:
            return False

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> Dict[str, dict]:
        cached = self._cache.get("positions", self.TTL_POSITIONS)
        if cached is not None:
            return cached
        positions = {}
        for p in self._api.list_positions():
            positions[p.symbol] = {
                "qty": float(p.qty),
                "side": p.side,
                "avg_entry": float(p.avg_entry_price),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
            }
        self._cache.put("positions", positions)
        return positions

    def get_open_orders(self) -> List[dict]:
        """Alias for list_open_orders (used by web)."""
        return self.list_open_orders()

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
            self.invalidate_cache()
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
            self.invalidate_cache()
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
        cached = self._cache.get("open_orders", self.TTL_ORDERS)
        if cached is not None:
            return cached
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
        self._cache.put("open_orders", orders)
        return orders
