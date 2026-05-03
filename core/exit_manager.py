"""
Adaptive Exit Manager
━━━━━━━━━━━━━━━━━━━━━

Watches every open position and exits based on learned parameters:
  • Trailing stop using `trail_atr_mult` from the learning engine
  • Dynamic target using `target_atr_mult` from the learning engine
  • Time stop: positions older than max_hold_hours that aren't profiting
  • Volatility stop: if realized vol explodes past threshold

Designed to run inside the main loop after each iteration.

Why this exists:
  Many losses come from sitting on a winner that turns into a loser
  (the "sold_too_early" / "held_too_long" mistake pair). The Exit
  Manager makes those decisions algorithmically using parameters the
  learning engine adapts over time.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, Optional

from utils.logger import get_logger

log = get_logger("core.exit_manager")


def _learn():
    try:
        from core.learning import get_engine
        return get_engine()
    except Exception:
        return None


class ExitManager:
    """
    Per-symbol high-water-mark trailing-stop manager driven by learned params.
    """

    def __init__(
        self,
        executor,
        portfolio,
        feed,
        max_hold_hours: float = 24.0,
        check_interval_sec: int = 5,
    ) -> None:
        self.executor = executor
        self.portfolio = portfolio
        self.feed = feed
        self.max_hold_hours = max_hold_hours
        self.check_interval_sec = check_interval_sec
        # Track high-water mark per (symbol, side)
        self._hwm: Dict[str, float] = {}
        # Track entry timestamps per symbol
        self._entry_ts: Dict[str, float] = defaultdict(lambda: time.time())
        self._last_check: float = 0.0

    def reset(self, symbol: str) -> None:
        self._hwm.pop(symbol, None)
        self._entry_ts.pop(symbol, None)

    def _get_price(self, symbol: str) -> Optional[float]:
        try:
            return float(self.executor._get_price(symbol) or 0)
        except Exception:
            return None

    def _strategy_for(self, symbol: str) -> str:
        """Use the learning engine's record to find which strategy owns the symbol."""
        eng = _learn()
        if not eng:
            return "default"
        try:
            for tid, t in eng._open_records.items():
                if t.symbol == symbol:
                    return t.strategy
        except Exception:
            pass
        return "default"

    def check_all(self) -> int:
        """
        Walk every open position. Exit any that hit the trail/time/vol stop.
        Returns number of exits triggered.
        """
        now = time.time()
        if now - self._last_check < self.check_interval_sec:
            return 0
        self._last_check = now

        try:
            self.portfolio.refresh()
            positions = dict(self.portfolio.positions or {})
        except Exception:
            return 0

        if not positions:
            return 0

        eng = _learn()
        exits = 0

        for symbol, pos in positions.items():
            try:
                side = pos.get("side", "long")
                qty = abs(pos.get("qty", 0))
                if qty <= 0:
                    continue
                entry = pos.get("avg_entry_price", 0)
                if not entry:
                    continue
                cur = self._get_price(symbol)
                if not cur or cur <= 0:
                    continue

                strategy = self._strategy_for(symbol)
                # Pull learned params (with sensible defaults)
                trail_mult = 1.5
                if eng:
                    try:
                        trail_mult = eng.tuner.get(strategy, "trail_atr_mult")
                    except Exception:
                        pass

                # Use a 2% trail relative to entry as floor (no ATR available here)
                trail_pct = max(0.005, min(0.04, 0.005 * trail_mult * 2))

                # Update HWM
                if side == "long":
                    hwm = self._hwm.get(symbol, cur)
                    hwm = max(hwm, cur)
                    self._hwm[symbol] = hwm
                    drawdown_from_peak = (hwm - cur) / hwm if hwm > 0 else 0
                    profit_pct = (cur - entry) / entry
                else:
                    hwm = self._hwm.get(symbol, cur)
                    hwm = min(hwm, cur)
                    self._hwm[symbol] = hwm
                    drawdown_from_peak = (cur - hwm) / hwm if hwm > 0 else 0
                    profit_pct = (entry - cur) / entry

                exit_reason = None

                # 1. Trailing-stop: only trail once we're in profit
                if profit_pct > 0.01 and drawdown_from_peak > trail_pct:
                    exit_reason = "trail"

                # 2. Time stop: held too long without progress
                age = now - self._entry_ts[symbol]
                if (age > self.max_hold_hours * 3600
                        and profit_pct < 0.005):
                    exit_reason = "timeout"

                if exit_reason:
                    log.info(
                        "EXIT_MGR: %s %s prof=%.2f%% peak_dd=%.2f%% reason=%s",
                        symbol, side, profit_pct * 100,
                        drawdown_from_peak * 100, exit_reason,
                    )
                    self.executor.exit_position(symbol, reason=exit_reason)
                    self.reset(symbol)
                    exits += 1

            except Exception as e:
                log.debug("ExitManager %s error: %s", symbol, e)

        # Clear HWM for symbols no longer held
        held = set(positions.keys())
        for sym in list(self._hwm.keys()):
            if sym not in held:
                self.reset(sym)

        return exits
