"""
Order executor — smart order routing with retry logic, slippage control,
bracket orders (stop-loss + take-profit), and fill tracking.

Uses Alpaca's native bracket order class to attach stop-loss and take-profit
as legs of a single order (avoids wash trade rejections).

Now also auto-records every fill to the learning engine so the bot
learns from every trade without changes to individual strategies.
"""
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

from config import CONFIG
from core.broker import Broker
from core.risk import RiskEngine
from core.portfolio import Portfolio
from utils.logger import get_logger

log = get_logger("core.executor")


def _learn():
    """Lazy import — keeps executor importable even if learning module missing."""
    try:
        from core.learning import get_engine
        return get_engine()
    except Exception:
        return None


class OrderExecutor:
    MAX_NEW_ENTRIES_PER_CYCLE = 3   # max new entries per cycle across all strategies

    def __init__(self, broker: Broker, risk: RiskEngine, portfolio: Portfolio) -> None:
        self._broker = broker
        self._risk = risk
        self._portfolio = portfolio
        self._cycle_lock = threading.Lock()
        self._cycle_entries = 0
        # symbol -> trade_id (for auto-close when position closes)
        self._symbol_to_trade_id: Dict[str, str] = {}
        self._known_positions: Dict[str, dict] = {}

    def reset_cycle(self) -> None:
        """Call at the start of each cycle to reset the per-cycle entry counter."""
        with self._cycle_lock:
            self._cycle_entries = 0

    # ── Auto-close detection (call once per main-loop iteration) ──────────────

    def detect_closures(self) -> None:
        """
        Compare current positions with last-known positions. Any symbol that
        was open and is now flat → record exit in the learning engine using
        the most recent trade price as exit price.
        """
        try:
            current = self._portfolio.positions or {}
        except Exception:
            return

        # Update MFE/MAE for still-open trades
        eng = _learn()
        if eng:
            def _price(sym: str) -> float:
                try:
                    return float(self._get_price(sym) or 0)
                except Exception:
                    return 0.0
            try:
                eng.update_open_trades(_price)
            except Exception:
                pass

        # Detect closures (was in known, no longer in current)
        closed_syms = [s for s in self._known_positions if s not in current]
        for sym in closed_syms:
            tid = self._symbol_to_trade_id.pop(sym, None)
            if not tid or not eng:
                continue
            exit_price = self._get_price(sym) or 0.0
            if exit_price <= 0:
                # Fall back to last known mark price
                exit_price = self._known_positions[sym].get("avg_entry_price", 0)
            try:
                eng.record_exit(tid, exit_price, exit_reason="auto_detected")
            except Exception:
                pass

        # Snapshot current positions
        self._known_positions = dict(current)

    # ── Public entry points ───────────────────────────────────────────────────

    def enter_long(self, symbol, qty, stop_price=None, take_profit=None,
                   strategy_tag="", confidence=0.5, signals=None, atr=None):
        result = self._execute(symbol, qty, "buy", stop_price, take_profit,
                               strategy_tag, confidence)
        if result and strategy_tag:
            self._record_learn_entry(strategy_tag, symbol, "long", qty,
                                     confidence, signals, stop_price,
                                     take_profit, atr)
        return result

    def enter_short(self, symbol, qty, stop_price=None, take_profit=None,
                    strategy_tag="", confidence=0.5, signals=None, atr=None):
        result = self._execute(symbol, qty, "sell", stop_price, take_profit,
                               strategy_tag, confidence)
        if result and strategy_tag:
            self._record_learn_entry(strategy_tag, symbol, "short", qty,
                                     confidence, signals, stop_price,
                                     take_profit, atr)
        return result

    def exit_position(self, symbol: str, reason: str = "") -> Optional[dict]:
        pos = self._portfolio.positions.get(symbol)
        if not pos:
            return None
        side = "sell" if pos["side"] == "long" else "buy"
        qty = abs(pos["qty"])
        log.info("Exiting %s (%s) qty=%s  reason=%s", symbol, pos["side"], qty, reason or "—")
        result = self._submit_with_retry(symbol, qty, side, "market")
        # Record exit in learning engine
        if result:
            tid = self._symbol_to_trade_id.pop(symbol, None)
            if tid:
                eng = _learn()
                if eng:
                    px = self._get_price(symbol) or pos.get("current_price", 0)
                    try:
                        eng.record_exit(tid, px, exit_reason=reason or "manual")
                    except Exception:
                        pass
        return result

    def _record_learn_entry(
        self, strategy_tag, symbol, side, qty, confidence,
        signals, stop_price, target_price, atr,
    ) -> None:
        """Persist a fresh entry to the learning engine."""
        eng = _learn()
        if not eng:
            return
        try:
            entry_price = self._get_price(symbol) or 0.0
            if entry_price <= 0:
                return
            # Pull regime if available via portfolio
            regime = "unknown"
            try:
                if hasattr(self._portfolio, "regime_name"):
                    regime = self._portfolio.regime_name
            except Exception:
                pass
            tid = eng.record_entry(
                strategy=strategy_tag,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                qty=qty,
                confidence=confidence or 0.5,
                signals=signals or {},
                regime=regime,
                stop_price=stop_price,
                target_price=target_price,
                atr=atr,
            )
            if tid:
                self._symbol_to_trade_id[symbol] = tid
        except Exception as e:
            log.debug("learn entry record failed for %s: %s", symbol, e)

    def place_limit_pair(self, symbol, bid, ask, qty):
        """Market-making: place limit buy at bid and limit sell at ask.
        Alpaca rejects opposing orders on the same symbol if no existing position,
        so only place sell side if we hold inventory."""
        # Skip MM entirely if we're already short this symbol (wash trade / conflict)
        pos = self._portfolio.positions.get(symbol)
        if pos and pos.get("side") == "short":
            return None, None

        # Hard cap: MM buy should not exceed $4K crypto / $8K stocks
        MAX_MM_DOLLARS = 4_000 if "/" in symbol else 8_000
        order_cost = qty * bid
        if order_cost > MAX_MM_DOLLARS:
            new_qty = MAX_MM_DOLLARS / bid
            if "/" in symbol:
                qty = round(new_qty, 4)  # crypto fractional
            else:
                qty = max(1, int(new_qty))
            order_cost = qty * bid

        # Check cash before placing MM buy
        try:
            if "/" in symbol:
                avail = self._portfolio.cash
            else:
                avail = self._portfolio.buying_power
            if order_cost > avail * 0.85:
                log.debug("MM SKIP %s buy — order $%.0f > avail $%.0f", symbol, order_cost, avail)
                return None, None
        except Exception:
            pass

        buy_order = self._submit_with_retry(symbol, qty, "buy", "limit", limit_price=bid)
        # Only place sell if we have an existing position (otherwise Alpaca blocks short)
        pos = self._portfolio.positions.get(symbol)
        if pos and pos.get("qty", 0) > 0:
            sell_order = self._submit_with_retry(symbol, qty, "sell", "limit", limit_price=ask)
        else:
            sell_order = None
        return buy_order, sell_order

    # ── Core execution ────────────────────────────────────────────────────────

    def _execute(self, symbol, qty, side, stop_price, take_profit, tag,
                 confidence=0.5):
        self._portfolio.refresh()
        price = self._get_price(symbol)
        if price is None:
            log.error("Cannot get price for %s — skipping", symbol)
            return None

        # ── Virtual $5K cap: never deploy more than VIRTUAL_CAP total ─────────
        from config import CONFIG
        VIRTUAL_CAP = getattr(CONFIG, 'virtual_cap', 5000.0)
        total_deployed = sum(abs(p.get("market_value", 0)) for p in self._portfolio.positions.values())
        remaining_cap = max(0, VIRTUAL_CAP - total_deployed)
        if remaining_cap <= 10:
            log.warning("Virtual cap $%.0f fully deployed ($%.0f in positions) — skipping %s",
                        VIRTUAL_CAP, total_deployed, symbol)
            return None

        # Per-trade hard cap — capped by remaining virtual budget
        is_crypto = "/" in symbol
        MAX_ORDER_DOLLARS = min(remaining_cap, 1000 if is_crypto else 1500)  # Max $1K crypto / $1.5K stock per trade
        order_cost = qty * price
        if order_cost > MAX_ORDER_DOLLARS:
            if is_crypto:
                new_qty = round(MAX_ORDER_DOLLARS / price, 4)
            else:
                new_qty = int(MAX_ORDER_DOLLARS / price)
            if new_qty < (0.0001 if is_crypto else 1):
                log.warning("Trade REJECTED [%s] %s %s: price $%.2f > per-trade cap", tag, side, symbol, price)
                return None
            log.info("Capping [%s] %s %s qty %s→%s (cap $%.0f)", tag, side, symbol, qty, new_qty, MAX_ORDER_DOLLARS)
            qty = new_qty
            order_cost = qty * price

        approved, reason = self._risk.pre_trade_check(
            symbol, qty, price, self._portfolio.equity,
            self._portfolio.positions, confidence=confidence
        )
        if not approved:
            log.warning("Trade REJECTED [%s] %s %s: %s", tag, side, symbol, reason)
            return None

        # Live buying-power check from broker — freshest data
        try:
            acct = self._broker.get_account()
            if is_crypto:
                # Crypto: Alpaca uses actual cash (no margin)
                cash_avail = float(acct.get("cash", 0))
                if side == "buy" and order_cost > cash_avail * 0.70:
                    log.warning("Trade REJECTED [%s] %s %s: order $%.0f > 70%% cash $%.0f",
                                tag, side, symbol, order_cost, cash_avail)
                    return None
            else:
                bp = float(acct.get("buying_power", 0))
                # Leave at least $500 buffer; use up to 70% of available BP per trade
                if order_cost > max(bp * 0.70, bp - 500):
                    log.warning("Trade REJECTED [%s] %s %s: order $%.0f > 70%% BP $%.0f",
                                tag, side, symbol, order_cost, bp)
                    return None
        except Exception:
            pass

        # Skip if conflicting open order exists (e.g., MM buy blocks short-sell)
        try:
            open_orders = self._broker.list_open_orders()
            for o in open_orders:
                if o.get("symbol") == symbol and o.get("side") != side:
                    log.debug("Skipping [%s] %s %s — opposing open order exists", tag, side, symbol)
                    return None
        except Exception:
            pass

        # Block stock entries outside market hours (crypto trades 24/7)
        if not is_crypto:
            try:
                if not self._broker.is_market_open():
                    log.debug("Market closed — skipping [%s] %s %s", tag, side, symbol)
                    return None
            except Exception:
                pass

        # Per-cycle entry limit — prevents simultaneous signals from draining buying power
        with self._cycle_lock:
            if self._cycle_entries >= self.MAX_NEW_ENTRIES_PER_CYCLE:
                log.debug("Cycle entry limit (%d) reached — skipping [%s] %s %s",
                          self.MAX_NEW_ENTRIES_PER_CYCLE, tag, side, symbol)
                return None
            self._cycle_entries += 1

        conf_label = f"CONF={confidence:.0%}" if confidence != 0.5 else ""
        log.info("[%s] %s %s %s qty=%s  stop=%s  tp=%s  %s",
                 tag, side.upper(), qty, symbol, qty, stop_price, take_profit,
                 conf_label)

        # Bracket orders only for BUY entries — sell-side brackets unreliable on Alpaca
        if (stop_price or take_profit) and side == "buy":
            order = self._submit_bracket(
                symbol, qty, side,
                stop_price=stop_price,
                take_profit_price=take_profit,
            )
            if order:
                return order
            log.warning("Bracket failed for %s, falling back to simple order", symbol)

        # Simple limit or market order (no bracket legs)
        order_type = CONFIG.order_type
        limit_price = None
        if order_type in ("limit", "smart"):
            if side == "buy":
                limit_price = round(price * (1 + CONFIG.slippage_tolerance), 2)
            else:
                limit_price = round(price * (1 - CONFIG.slippage_tolerance), 2)
            order_type = "limit"
        return self._submit_with_retry(symbol, qty, side, order_type, limit_price=limit_price)

    def _submit_bracket(self, symbol, qty, side, stop_price=None, take_profit_price=None):
        """Bracket order: MARKET entry + attached SL/TP as one atomic unit."""
        cid = f"qb_{symbol}_{side}_{uuid.uuid4().hex[:8]}"
        result = self._broker.submit_bracket_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type="market",  # market entry avoids base_price validation issues
            limit_price=None,
            stop_loss_price=stop_price,
            take_profit_price=take_profit_price,
            client_order_id=cid,
        )
        return result

    def _submit_with_retry(self, symbol, qty, side, order_type,
                           limit_price=None, stop_price=None, tif="day"):
        # Crypto requires gtc — "day" is not a valid crypto TIF on Alpaca
        effective_tif = "gtc" if "/" in symbol else tif
        cid = f"qb_{symbol}_{side}_{uuid.uuid4().hex[:8]}"
        for attempt in range(1, CONFIG.max_retries + 1):
            result = self._broker.submit_order(
                symbol=symbol, qty=qty, side=side,
                order_type=order_type, time_in_force=effective_tif,
                limit_price=limit_price, stop_price=stop_price,
                client_order_id=cid,
            )
            if result:
                return result
            log.warning("Order attempt %d/%d failed for %s", attempt, CONFIG.max_retries, symbol)
            time.sleep(1.5 * attempt)
        log.error("All %d order attempts exhausted for %s", CONFIG.max_retries, symbol)
        return None

    def _get_price(self, symbol: str) -> Optional[float]:
        try:
            if "/" in symbol:
                trades = self._broker._api.get_latest_crypto_trades(symbol)
                t = trades.get(symbol)
                price = t.p if t else None
            else:
                price = self._broker._api.get_latest_trade(symbol).p
            return float(price)
        except Exception as e:
            log.error("_get_price(%s): %s", symbol, e)
            return None
