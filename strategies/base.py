"""
Base strategy — regime + VPIN + Learning Engine aware.

All strategies inherit from this. New helpers:
  • learn_before_entry(...)  → returns (adjusted_conf, size_mult, veto, reason)
  • learn_record_entry(...)  → persists trade for the learning engine
  • learn_record_exit(...)   → closes trade in learning engine, triggers adapt
  • learn_param(name, default) → reads adapted parameter (e.g. stop_atr_mult)
  • learn_track_position(symbol, current_price) → updates MFE/MAE
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from core.executor import OrderExecutor
from core.portfolio import Portfolio
from core.regime import RegimeDetector
from core.risk import RiskEngine
from core.vpin import VPINMonitor
from data.feed import MarketDataFeed
from utils.logger import get_logger


class BaseStrategy(ABC):
    name: str = "base"

    def __init__(
        self,
        feed: MarketDataFeed,
        executor: OrderExecutor,
        portfolio: Portfolio,
        risk: RiskEngine,
        regime: Optional[RegimeDetector] = None,
        vpin: Optional[VPINMonitor] = None,
    ) -> None:
        self.feed = feed
        self.executor = executor
        self.portfolio = portfolio
        self.risk = risk
        self.regime = regime
        self.vpin = vpin
        self.log = get_logger(f"strategy.{self.name}")
        # Map symbol -> trade_id for active positions opened by this strategy
        self._open_trade_ids: Dict[str, str] = {}

    @abstractmethod
    def get_symbols(self) -> List[str]:
        """Return the list of symbols this strategy trades."""

    def update_symbols(self, symbols: List[str]) -> None:
        """Dynamically expand the symbol universe from the full market scanner.

        Called each cycle with the merged list of hot scanner picks + static
        config universe. Subclasses that maintain their own symbol list should
        override this to merge/replace it; the default is a no-op so existing
        strategies continue to work unchanged.
        """
        pass

    @abstractmethod
    def run(self) -> None:
        """Execute one iteration of the strategy logic."""

    # ── Shared helpers ────────────────────────────────────────────────────────

    @property
    def regime_scale(self) -> float:
        if self.regime is not None:
            return self.regime.position_scale
        return 1.0

    @property
    def regime_name(self) -> str:
        if self.regime is not None and hasattr(self.regime, "state_name"):
            return self.regime.state_name
        return "unknown"

    def is_symbol_toxic(self, symbol: str) -> bool:
        if self.vpin is not None:
            return self.vpin.is_toxic(symbol)
        return False

    _cached_pending_symbols: set = set()
    _cached_pending_ts: float = 0.0

    def _refresh_pending_cache(self) -> None:
        """Cache pending orders for 5 seconds — avoids API call per symbol."""
        now = time.time()
        if now - self._cached_pending_ts < 5.0:
            return
        try:
            open_orders = self.executor._broker.list_open_orders()
            self.__class__._cached_pending_symbols = {o["symbol"] for o in open_orders}
        except Exception:
            self.__class__._cached_pending_symbols = set()
        self.__class__._cached_pending_ts = now

    def has_pending_order(self, symbol: str) -> bool:
        """Check if there's already a pending order (uses 5s cache)."""
        self._refresh_pending_cache()
        return symbol in self._cached_pending_symbols

    def should_skip_entry(self, symbol: str) -> bool:
        """Combined gate: risk halted OR VPIN toxic OR pending order exists."""
        if self.risk.is_halted:
            return True
        if self.has_pending_order(symbol):
            self.log.debug("Skipping %s — pending order exists", symbol)
            return True
        if self.is_symbol_toxic(symbol):
            self.log.debug("Skipping %s — VPIN toxic", symbol)
            return True
        return False

    def _kelly_qty(self, symbol: str, price: float, win_rate: float = 0.55,
                   avg_win: float = 0.02, avg_loss: float = 0.01) -> float:
        return self.risk.kelly_size(
            self.portfolio.equity, win_rate, avg_win, avg_loss, price
        )

    # ── Learning engine integration ───────────────────────────────────────────
    # All optional — strategies that don't call these still work fine.

    def _engine(self):
        try:
            from core.learning import get_engine
            return get_engine()
        except Exception:
            return None

    def learn_param(self, name: str, default: float) -> float:
        """Get a tuned parameter from the learning engine, or default."""
        eng = self._engine()
        if not eng:
            return default
        try:
            return eng.tuner.get(self.name, name)
        except Exception:
            return default

    def llm_decide(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        atr: float,
        signals: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Ask the LLM brain (Claude or Grok) to ratify a candidate trade.
        Returns a dict from Decision.to_dict() or None if disabled.

        Strategies should treat veto = decision.action != 'enter'.
        """
        try:
            from core.llm_brain import get_brain
        except Exception:
            return None
        brain = get_brain()
        if not brain.has_provider:
            # Skip the call entirely if no provider configured (avoid budget waste)
            return None

        # Pull recent stats to feed the prompt
        wr = None
        mistakes = None
        eng = self._engine()
        if eng:
            try:
                stats = eng.store.stats_by_strategy().get(self.name) or {}
                wr = stats.get("win_rate")
                mistakes = eng.store.mistake_distribution(days=14)
            except Exception:
                pass

        portfolio_ctx = {}
        try:
            portfolio_ctx = {
                "equity":   round(self.portfolio.equity, 2),
                "cash":     round(self.portfolio.cash, 2),
                "n_positions": len(self.portfolio.positions or {}),
            }
        except Exception:
            pass

        try:
            dec = brain.decide(
                strategy=self.name, symbol=symbol, side=side,
                entry_price=entry_price, atr=atr, signals=signals,
                regime=self.regime_name,
                recent_winrate=wr, recent_mistakes=mistakes,
                portfolio_context=portfolio_ctx,
            )
            return dec.to_dict()
        except Exception as e:
            self.log.debug("llm_decide error: %s", e)
            return None

    def learn_before_entry(
        self,
        signals: Dict[str, float],
        base_confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Ask the learning engine whether to enter and how big.
        Returns:
            {"confidence": float, "size_mult": float, "veto": bool, "reason": str}
        Strategies should:
            res = self.learn_before_entry(signals, base_conf)
            if res["veto"]:
                self.log.debug("learn-veto %s: %s", symbol, res["reason"])
                return
            qty = base_qty * res["size_mult"]
            confidence = res["confidence"]
        """
        eng = self._engine()
        if not eng:
            return {"confidence": base_confidence, "size_mult": 1.0,
                    "veto": False, "reason": "learning disabled"}
        try:
            adj, size_mult, veto, reason = eng.before_entry(
                self.name, signals, self.regime_name, base_confidence
            )
            return {"confidence": adj, "size_mult": size_mult,
                    "veto": veto, "reason": reason}
        except Exception as e:
            return {"confidence": base_confidence, "size_mult": 1.0,
                    "veto": False, "reason": f"learn-err:{e}"}

    def learn_record_entry(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        confidence: float,
        signals: Dict[str, float],
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        atr: Optional[float] = None,
    ) -> Optional[str]:
        """Persist a new trade entry to the learning engine. Returns trade_id."""
        eng = self._engine()
        if not eng:
            return None
        try:
            tid = eng.record_entry(
                strategy=self.name,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                qty=qty,
                confidence=confidence,
                signals=signals or {},
                regime=self.regime_name,
                stop_price=stop_price,
                target_price=target_price,
                atr=atr,
            )
            if tid:
                self._open_trade_ids[symbol] = tid
            return tid
        except Exception as e:
            self.log.warning("learn_record_entry error: %s", e)
            return None

    def learn_record_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "manual",
    ) -> Optional[Dict[str, Any]]:
        """Close out a trade in the learning engine. Pops from _open_trade_ids."""
        eng = self._engine()
        if not eng:
            return None
        tid = self._open_trade_ids.pop(symbol, None)
        if not tid:
            return None
        try:
            return eng.record_exit(tid, exit_price, exit_reason)
        except Exception as e:
            self.log.warning("learn_record_exit error: %s", e)
            return None

    def learn_track_positions(self) -> None:
        """Update MFE/MAE for all open trades opened by this strategy."""
        eng = self._engine()
        if not eng or not self._open_trade_ids:
            return

        def _price(sym: str) -> float:
            try:
                return float(self.executor._get_price(sym) or 0)
            except Exception:
                return 0.0

        try:
            eng.update_open_trades(_price)
        except Exception:
            pass
