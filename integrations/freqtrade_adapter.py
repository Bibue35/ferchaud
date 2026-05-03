"""
Freqtrade Adapter — bridge Ferchaud's AI brain into a Freqtrade instance
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Why this exists:
  Freqtrade is a battle-tested open-source crypto trading platform with
  hundreds of pre-built strategies, exchange connectors, backtesting,
  and dry-run mode. Building all of that from scratch is a waste of
  time. Instead, we run Freqtrade as the "execution engine" and
  Ferchaud as the "intelligence layer" that:

    1. Consumes Freqtrade's native signals via its REST API
    2. Routes them through our LLM brain (Claude / Grok) for ratification
    3. Tracks every trade in Ferchaud's learning engine
    4. Pushes back accept/veto decisions via Freqtrade's force_entry/exit endpoints

Usage:
    adapter = FreqtradeAdapter(
        base_url="http://localhost:8080",
        username="freqtrade",
        password="freqtrade",
    )
    adapter.tick()  # call from main loop or a cron

Freqtrade REST API reference:
    https://www.freqtrade.io/en/stable/rest-api/

Environment variables:
    FREQTRADE_URL       — REST endpoint (default http://localhost:8080)
    FREQTRADE_USER      — basic auth username
    FREQTRADE_PASS      — basic auth password
    FREQTRADE_ENABLED   — "true" to activate the adapter in main loop
"""
from __future__ import annotations

import os
import time
import threading
from typing import Any, Dict, List, Optional

import requests

from utils.logger import get_logger

log = get_logger("integrations.freqtrade")


class FreqtradeAdapter:
    """REST client + brain wrapper for Freqtrade."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 8,
    ) -> None:
        self.base_url = (base_url or os.environ.get("FREQTRADE_URL", "http://localhost:8080")).rstrip("/")
        self.user = username or os.environ.get("FREQTRADE_USER", "")
        self.pwd = password or os.environ.get("FREQTRADE_PASS", "")
        self.timeout = timeout
        self._session = requests.Session()
        if self.user:
            self._session.auth = (self.user, self.pwd)
        self._lock = threading.RLock()
        self._known_trades: Dict[int, dict] = {}
        # Map freqtrade trade_id → ferchaud learning trade_id
        self._learn_trade_ids: Dict[int, str] = {}

    # ── HTTP wrappers ─────────────────────────────────────────────────────────

    def _get(self, path: str) -> Optional[Any]:
        try:
            r = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            log.debug("Freqtrade GET %s -> %s", path, r.status_code)
        except Exception as e:
            log.debug("Freqtrade GET %s error: %s", path, e)
        return None

    def _post(self, path: str, payload: dict) -> Optional[Any]:
        try:
            r = self._session.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
            if r.status_code in (200, 201):
                return r.json()
            log.debug("Freqtrade POST %s -> %s: %s", path, r.status_code, r.text[:200])
        except Exception as e:
            log.debug("Freqtrade POST %s error: %s", path, e)
        return None

    # ── Health / info ─────────────────────────────────────────────────────────

    def ping(self) -> bool:
        return self._get("/api/v1/ping") is not None

    def status(self) -> Optional[List[dict]]:
        return self._get("/api/v1/status")

    def whitelist(self) -> List[str]:
        d = self._get("/api/v1/whitelist") or {}
        return d.get("whitelist", [])

    def open_trades(self) -> List[dict]:
        return self.status() or []

    def show_config(self) -> Optional[dict]:
        return self._get("/api/v1/show_config")

    def profit(self) -> Optional[dict]:
        """Aggregate profit summary."""
        return self._get("/api/v1/profit")

    # ── Force entry / exit ────────────────────────────────────────────────────

    def force_entry(self, pair: str, side: str = "long",
                    stake_amount: Optional[float] = None,
                    enter_tag: str = "ferchaud_brain") -> Optional[dict]:
        """Manually open a position via Freqtrade. Side: 'long' | 'short'."""
        payload: Dict[str, Any] = {"pair": pair, "side": side, "entry_tag": enter_tag}
        if stake_amount is not None:
            payload["stakeamount"] = stake_amount
        return self._post("/api/v1/forceenter", payload)

    def force_exit(self, trade_id: int, ordertype: str = "market") -> Optional[dict]:
        return self._post(
            "/api/v1/forceexit",
            {"tradeid": str(trade_id), "ordertype": ordertype},
        )

    def reload_config(self) -> Optional[dict]:
        return self._post("/api/v1/reload_config", {})

    # ── Brain integration ─────────────────────────────────────────────────────

    def review_open_trades(self) -> List[dict]:
        """
        For every open Freqtrade trade, ask the LLM brain whether to hold or
        force-exit. Returns a list of decisions taken.

        The brain's veto here is conservative — it only forces an exit when
        confidence in continued upside is < 0.3 AND open P&L is negative.
        """
        try:
            from core.llm_brain import get_brain
        except Exception:
            return []
        brain = get_brain()
        if not brain.has_provider:
            return []

        trades = self.open_trades() or []
        decisions = []
        for t in trades:
            try:
                pair = t.get("pair")
                tid = t.get("trade_id")
                profit_ratio = float(t.get("profit_ratio") or 0)
                signals = {
                    "open_pnl": round(profit_ratio, 4),
                    "duration": t.get("trade_duration_s") or 0,
                    "stake":    t.get("stake_amount") or 0,
                }
                dec = brain.decide(
                    strategy="freqtrade",
                    symbol=pair,
                    side=t.get("trade_direction") or "long",
                    entry_price=float(t.get("open_rate") or 0),
                    atr=0.0,
                    signals=signals,
                    regime="unknown",
                )
                # Only force exit when brain explicitly recommends it AND we're losing
                should_exit = (
                    dec.action == "skip"
                    and dec.confidence < 0.3
                    and profit_ratio < 0
                )
                if should_exit and tid is not None:
                    self.force_exit(tid)
                    decisions.append({"trade": tid, "exited": True,
                                      "reason": dec.rationale})
                else:
                    decisions.append({"trade": tid, "exited": False,
                                      "rationale": dec.rationale})
            except Exception as e:
                log.debug("review trade error: %s", e)
                continue
        return decisions

    def journal_to_learning(self) -> int:
        """
        Mirror Freqtrade's open + closed trades into Ferchaud's learning
        engine so all trades feed the same brain. Returns count synced.
        """
        try:
            from core.learning import get_engine
        except Exception:
            return 0
        eng = get_engine()
        synced = 0

        # Mirror open trades as "entries"
        for t in self.open_trades():
            tid = t.get("trade_id")
            if tid in self._learn_trade_ids:
                continue
            pair = t.get("pair")
            try:
                ftid = eng.record_entry(
                    strategy="freqtrade",
                    symbol=pair,
                    side=(t.get("trade_direction") or "long").lower(),
                    entry_price=float(t.get("open_rate") or 0),
                    qty=float(t.get("amount") or 0),
                    confidence=0.5,
                    signals={
                        "freqtrade_entry_tag": t.get("enter_tag") or "default",
                    },
                    regime="unknown",
                    stop_price=float(t.get("stop_loss_abs") or 0) or None,
                    target_price=None,
                )
                if ftid:
                    self._learn_trade_ids[tid] = ftid
                    synced += 1
            except Exception as e:
                log.debug("journal entry %s error: %s", tid, e)

        # Detect closures: trades we knew about but are no longer open
        current_ids = {t.get("trade_id") for t in self.open_trades()}
        for ftid_known in list(self._learn_trade_ids.keys()):
            if ftid_known not in current_ids:
                tid = self._learn_trade_ids.pop(ftid_known)
                # We don't have exit price reliably here without /trades endpoint
                # so use a no-op exit. The full /trades query would give exit info.
                eng.record_exit(tid, exit_price=0.0, exit_reason="freqtrade_closed")

        return synced

    # ── Tick (call from main loop or cron) ───────────────────────────────────

    def tick(self) -> Dict[str, Any]:
        """One iteration: review trades + sync to learning engine."""
        if not self.ping():
            return {"ok": False, "reason": "freqtrade unreachable"}
        decisions = self.review_open_trades()
        synced = self.journal_to_learning()
        return {"ok": True, "decisions": len(decisions), "synced": synced}


# ── Singleton ─────────────────────────────────────────────────────────────────

_ADAPTER: Optional[FreqtradeAdapter] = None
_ADAPTER_LOCK = threading.Lock()


def get_freqtrade_adapter() -> FreqtradeAdapter:
    global _ADAPTER
    with _ADAPTER_LOCK:
        if _ADAPTER is None:
            _ADAPTER = FreqtradeAdapter()
        return _ADAPTER


def is_freqtrade_enabled() -> bool:
    return os.environ.get("FREQTRADE_ENABLED", "false").lower() in ("true", "1", "yes")
