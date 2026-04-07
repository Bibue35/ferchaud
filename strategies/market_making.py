"""
Market Making Strategy (v2 — Regime + VPIN aware)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Posts simultaneous limit orders on both sides of the book.

v2 enhancements:
  • VPIN awareness: widen spreads or halt quoting when toxicity spikes
  • Regime: in CRISIS, widen spreads 2× and reduce inventory limits
  • Dynamic spread = ATR-based + inventory skew + toxicity adjustment
"""
from typing import List

import numpy as np

from config import CONFIG
from data.indicators import atr
from strategies.base import BaseStrategy


MAX_INVENTORY = 50
INVENTORY_SKEW = 0.3
QUOTE_SYMBOLS = ["AAPL", "MSFT", "BTC/USD", "ETH/USD"]
# Max dollar exposure per crypto symbol as % of portfolio equity
MAX_CRYPTO_EXPOSURE_PCT = 0.20   # 20% of equity per symbol ($20K on $100K account)


class MarketMakingStrategy(BaseStrategy):
    name = "market_making"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_orders: dict = {}

    def get_symbols(self) -> List[str]:
        return [s for s in QUOTE_SYMBOLS
                if s in CONFIG.stock_universe + CONFIG.crypto_universe]

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk engine halted — pulling all MM quotes")
            self._cancel_all()
            return

        for symbol in self.get_symbols():
            try:
                self._process(symbol)
            except Exception as exc:
                self.log.error("MM error for %s: %s", symbol, exc)

    def _process(self, symbol: str) -> None:
        self._cancel_stale(symbol)

        # If VPIN is toxic, don't quote — pull out
        if self.is_symbol_toxic(symbol):
            self.log.warning("MM halting quotes for %s — VPIN toxic", symbol)
            return

        quote = self.feed.get_latest_quote(symbol)
        if quote is None:
            return
        bid_mkt, ask_mkt = quote["bid"], quote["ask"]
        if bid_mkt <= 0 or ask_mkt <= 0:
            return
        mid = (bid_mkt + ask_mkt) / 2.0

        # ATR-based half-spread
        df = self.feed.get_bars(symbol, timeframe="5Min", limit=60)
        if df.empty:
            return
        atr_val = atr(df["high"], df["low"], df["close"], 14).iloc[-1]
        half_spread = float(np.clip(atr_val * 0.5, mid * 0.0005, mid * 0.005))

        # Regime adjustment: widen in crisis
        from core.regime import CRISIS, SIDEWAYS
        if self.regime:
            if self.regime.current_state == CRISIS:
                half_spread *= 2.0
            elif self.regime.current_state == SIDEWAYS:
                half_spread *= 1.3

        # VPIN soft adjustment: widen when approaching threshold
        if self.vpin:
            cdf = self.vpin.get_vpin_cdf(symbol)
            if cdf and cdf > 0.6:
                # Linear widening: 1.0× at 60%, 2.0× at threshold
                toxicity_mult = 1.0 + (cdf - 0.6) / (CONFIG.vpin_halt_threshold - 0.6)
                half_spread *= toxicity_mult

        # Inventory skew
        inventory = self.portfolio.position_qty(symbol)
        max_inv = MAX_INVENTORY
        if self.regime and self.regime.current_state == CRISIS:
            max_inv = MAX_INVENTORY // 2

        skew = (inventory / max_inv) * INVENTORY_SKEW * half_spread if max_inv > 0 else 0

        our_bid = round(mid - half_spread - skew, 4)
        our_ask = round(mid + half_spread - skew, 4)

        if our_bid <= 0 or our_ask <= 0 or our_bid >= our_ask:
            return

        # Dollar-based cap for crypto: flatten if exposure > MAX_CRYPTO_EXPOSURE_PCT
        if "/" in symbol:
            alpaca_sym = symbol.replace("/", "")
            pos_data = self.portfolio.positions.get(alpaca_sym, {})
            current_exposure = abs(float(pos_data.get("market_value", 0) or 0))
            max_exposure = self.portfolio.equity * MAX_CRYPTO_EXPOSURE_PCT
            if current_exposure >= max_exposure:
                self.log.warning(
                    "MM: %s exposure $%.0f >= limit $%.0f — reducing inventory",
                    symbol, current_exposure, max_exposure,
                )
                if current_exposure >= max_exposure * 1.2:
                    self.executor.exit_position(symbol, reason="MM: dollar cap")
                return  # Skip placing new bid; only let existing ask run

        # Flatten excess inventory (unit-based, for stocks)
        if abs(inventory) >= max_inv:
            self.executor.exit_position(symbol, reason="MM: max inventory")
            return

        qty = max(1, int((self.portfolio.equity * 0.01 * self.regime_scale) / mid))
        self.log.info(
            "MM quote %s  bid=%.4f  ask=%.4f  spread=%.4f  inv=%.0f  qty=%d",
            symbol, our_bid, our_ask, our_ask - our_bid, inventory, qty,
        )

        buy_order, sell_order = self.executor.place_limit_pair(symbol, our_bid, our_ask, qty)
        buy_id = buy_order["id"] if buy_order else None
        sell_id = sell_order["id"] if sell_order else None
        self._active_orders[symbol] = (buy_id, sell_id)

    def _cancel_stale(self, symbol: str) -> None:
        ids = self._active_orders.pop(symbol, (None, None))
        for order_id in ids:
            if order_id:
                self.executor._broker.cancel_order(order_id)

    def _cancel_all(self) -> None:
        for symbol in list(self._active_orders.keys()):
            self._cancel_stale(symbol)
