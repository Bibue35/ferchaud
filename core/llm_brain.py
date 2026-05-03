"""
LLM Decision Brain — Claude + Grok layered on top of the local engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Architecture (per the user's pivot):
  Local strategies + indicators → produce CANDIDATE signals
  ↓
  LLM Brain (this module)        → ratifies, vetoes, explains, sizes
  ↓
  Risk + Learning engines        → final size & journal

The LLM is the "judgement layer" — it never blindly trades. It always
sees the full candidate signal context plus recent performance and
returns a structured JSON decision:

    {
      "action":     "enter" | "skip" | "wait",
      "side":       "long" | "short",
      "confidence": 0..1,
      "size_mult":  0.3..2.0,
      "rationale":  "Why I think this is/isn't a good trade",
      "risks":      ["..."],
    }

Provider fallback:
  1. Anthropic (Claude opus / sonnet / haiku) if ANTHROPIC_API_KEY set
  2. xAI Grok if XAI_API_KEY set
  3. Local rule-based fallback (no LLM) — never blocks the bot

Cost control:
  • Decisions cached for 60 seconds per (symbol, side) pair
  • Hard daily call budget configurable via LLM_DAILY_BUDGET
  • Async fire-and-forget option for non-blocking strategy execution
  • Compact prompt: only the most decision-relevant signals included
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from utils.logger import get_logger

log = get_logger("core.llm_brain")


# ═══════════════════════════════════════════════════════════════════════════════
#  Provider config
# ═══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_BASE    = "https://api.anthropic.com/v1"

XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_MODEL   = os.environ.get("XAI_MODEL", "grok-4-1-fast")
XAI_BASE    = "https://api.x.ai/v1"

DAILY_BUDGET = int(os.environ.get("LLM_DAILY_BUDGET", "500"))  # max calls/day
DECISION_TTL = int(os.environ.get("LLM_DECISION_TTL", "60"))   # cache seconds

REQUEST_TIMEOUT = 10  # seconds — short so the bot doesn't stall


# ═══════════════════════════════════════════════════════════════════════════════
#  Decision dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Decision:
    action: str = "skip"          # enter | skip | wait
    side: str = "long"            # long | short
    confidence: float = 0.0       # 0..1
    size_mult: float = 1.0        # 0.3..2.0
    rationale: str = ""
    risks: List[str] = field(default_factory=list)
    provider: str = "fallback"    # claude | grok | fallback
    elapsed_ms: int = 0
    cached: bool = False

    @property
    def should_enter(self) -> bool:
        return self.action == "enter" and self.confidence >= 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "side": self.side,
            "confidence": round(self.confidence, 3),
            "size_mult": round(self.size_mult, 3),
            "rationale": self.rationale,
            "risks": self.risks,
            "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
            "cached": self.cached,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Daily budget tracker
# ═══════════════════════════════════════════════════════════════════════════════

class _Budget:
    def __init__(self, daily_limit: int) -> None:
        self.daily_limit = daily_limit
        self._date = date.today().isoformat()
        self._count = 0
        self._lock = threading.Lock()

    def can_spend(self) -> bool:
        with self._lock:
            today = date.today().isoformat()
            if today != self._date:
                self._date = today
                self._count = 0
            return self._count < self.daily_limit

    def spend(self) -> None:
        with self._lock:
            self._count += 1

    @property
    def used(self) -> int:
        return self._count

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self._count)


# ═══════════════════════════════════════════════════════════════════════════════
#  TTL decision cache
# ═══════════════════════════════════════════════════════════════════════════════

class _DecisionCache:
    def __init__(self, max_size: int = 200) -> None:
        self._d: "OrderedDict[str, tuple]" = OrderedDict()
        self._max = max_size
        self._lock = threading.RLock()

    def get(self, key: str, ttl: int) -> Optional[Decision]:
        with self._lock:
            entry = self._d.get(key)
            if not entry:
                return None
            ts, dec = entry
            if time.time() - ts > ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            dec.cached = True
            return dec

    def put(self, key: str, dec: Decision) -> None:
        with self._lock:
            self._d[key] = (time.time(), dec)
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  System prompt
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are the trade-decision brain of an algorithmic trading bot.

You receive a candidate trade with:
  - symbol, side, entry price, ATR
  - technical signals (RSI, BB, EMA, MACD etc.) already evaluated by the strategy
  - market regime (bull/sideways/crisis)
  - recent performance of this strategy (win rate, recent mistakes)
  - portfolio context (cash, exposure, open positions)

Your job: ratify or veto the trade. Be SKEPTICAL by default.
Reject choppy / low-conviction setups. Approve only clear, well-defined signals
with favorable risk/reward AND historical edge in similar conditions.

Respond ONLY with valid JSON:
{
  "action": "enter" | "skip" | "wait",
  "side": "long" | "short",
  "confidence": 0.0..1.0,
  "size_mult": 0.3..2.0,
  "rationale": "1-2 sentences explaining your decision",
  "risks": ["specific risk 1", "specific risk 2"]
}

Guidance:
  • size_mult > 1.0 only when win_rate > 60% on this signal combo AND regime is favorable
  • confidence < 0.5 means skip
  • In CRISIS regime, prefer skip unless setup is exceptional
  • If recent mistakes show 'sold_too_early', allow wider targets
  • If recent mistakes show 'false_signal', raise the bar to enter
  • Keep rationale concise — one or two short sentences max
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM Brain
# ═══════════════════════════════════════════════════════════════════════════════

class LLMBrain:
    def __init__(self) -> None:
        self.budget = _Budget(DAILY_BUDGET)
        self.cache = _DecisionCache()
        self._claude_ok = bool(ANTHROPIC_API_KEY)
        self._grok_ok = bool(XAI_API_KEY)
        # Recent decision log (last 50) for the dashboard
        self._recent: deque = deque(maxlen=50)
        self._recent_lock = threading.RLock()
        if self._claude_ok:
            log.info("LLMBrain: Claude provider ready (model=%s)", ANTHROPIC_MODEL)
        if self._grok_ok:
            log.info("LLMBrain: Grok provider ready (model=%s)", XAI_MODEL)
        if not self._claude_ok and not self._grok_ok:
            log.info("LLMBrain: no API keys — using local fallback only")

    @property
    def has_provider(self) -> bool:
        return self._claude_ok or self._grok_ok

    # ── Public ────────────────────────────────────────────────────────────────

    def decide(
        self,
        strategy: str,
        symbol: str,
        side: str,
        entry_price: float,
        atr: float,
        signals: Dict[str, Any],
        regime: str = "unknown",
        recent_winrate: Optional[float] = None,
        recent_mistakes: Optional[Dict[str, int]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> Decision:
        """
        Synchronously fetch a decision. Returns a Decision dataclass.
        Falls back to local rule-based decision if no provider works.
        """
        cache_key = f"{strategy}:{symbol}:{side}:{round(entry_price, 2)}"
        cached = self.cache.get(cache_key, DECISION_TTL)
        if cached is not None:
            return cached

        if not self.has_provider or not self.budget.can_spend():
            dec = self._fallback_decision(side, signals, regime, recent_winrate)
            self.cache.put(cache_key, dec)
            self._log_recent(strategy, symbol, side, dec)
            return dec

        prompt = self._build_user_prompt(
            strategy, symbol, side, entry_price, atr,
            signals, regime, recent_winrate, recent_mistakes,
            portfolio_context,
        )

        # Try Claude first if available, else Grok
        dec: Optional[Decision] = None
        t0 = time.time()
        if self._claude_ok:
            dec = self._claude_call(prompt, timeout)
        if dec is None and self._grok_ok:
            dec = self._grok_call(prompt, timeout)
        if dec is None:
            dec = self._fallback_decision(side, signals, regime, recent_winrate)

        dec.elapsed_ms = int((time.time() - t0) * 1000)
        self.budget.spend()
        self.cache.put(cache_key, dec)
        self._log_recent(strategy, symbol, side, dec)
        return dec

    def _log_recent(self, strategy: str, symbol: str, side: str, dec: "Decision") -> None:
        with self._recent_lock:
            self._recent.appendleft({
                "ts":         time.time(),
                "strategy":   strategy,
                "symbol":     symbol,
                "side":       side,
                "action":     dec.action,
                "confidence": round(dec.confidence, 2),
                "size_mult":  round(dec.size_mult, 2),
                "rationale":  dec.rationale,
                "provider":   dec.provider,
                "elapsed_ms": dec.elapsed_ms,
            })

    def recent_decisions(self, limit: int = 20) -> list:
        """Return the N most recent LLM decisions (for the dashboard)."""
        with self._recent_lock:
            return list(self._recent)[:limit]

    # ── Prompt building ───────────────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(
        strategy, symbol, side, entry_price, atr, signals, regime,
        recent_winrate, recent_mistakes, portfolio_context,
    ) -> str:
        # Compact JSON-style prompt — no fluff
        ctx = {
            "candidate": {
                "strategy": strategy,
                "symbol": symbol,
                "side": side,
                "entry_price": round(entry_price, 4),
                "atr": round(atr, 4),
                "signals": signals,
            },
            "market_regime": regime,
            "strategy_recent_winrate": recent_winrate,
            "strategy_recent_mistakes": recent_mistakes,
            "portfolio": portfolio_context or {},
        }
        return (
            "Evaluate this candidate trade and respond with the JSON schema "
            "from your system prompt:\n\n```json\n"
            + json.dumps(ctx, indent=2)
            + "\n```"
        )

    # ── Provider: Claude ──────────────────────────────────────────────────────

    def _claude_call(self, user_prompt: str, timeout: int) -> Optional[Decision]:
        try:
            r = requests.post(
                f"{ANTHROPIC_BASE}/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 400,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=timeout,
            )
            if r.status_code != 200:
                log.debug("Claude HTTP %s: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return self._parse_decision(text, "claude")
        except Exception as e:
            log.debug("Claude call error: %s", e)
            return None

    # ── Provider: Grok ────────────────────────────────────────────────────────

    def _grok_call(self, user_prompt: str, timeout: int) -> Optional[Decision]:
        try:
            r = requests.post(
                f"{XAI_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {XAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": XAI_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
                timeout=timeout,
            )
            if r.status_code != 200:
                log.debug("Grok HTTP %s: %s", r.status_code, r.text[:200])
                return None
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return self._parse_decision(text, "grok")
        except Exception as e:
            log.debug("Grok call error: %s", e)
            return None

    # ── Decision parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_decision(text: str, provider: str) -> Optional[Decision]:
        if not text:
            return None
        # Try to find a JSON block
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return None
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

        action = str(obj.get("action", "skip")).lower()
        if action not in ("enter", "skip", "wait"):
            action = "skip"
        side = str(obj.get("side", "long")).lower()
        if side not in ("long", "short"):
            side = "long"
        try:
            conf = float(obj.get("confidence", 0))
            sm = float(obj.get("size_mult", 1.0))
        except (TypeError, ValueError):
            conf, sm = 0.0, 1.0
        conf = max(0.0, min(1.0, conf))
        sm = max(0.3, min(2.0, sm))

        rationale = str(obj.get("rationale", ""))[:500]
        risks = obj.get("risks", []) or []
        if not isinstance(risks, list):
            risks = [str(risks)]
        risks = [str(r)[:200] for r in risks][:5]

        return Decision(
            action=action, side=side,
            confidence=conf, size_mult=sm,
            rationale=rationale, risks=risks,
            provider=provider,
        )

    # ── Fallback (no LLM) ─────────────────────────────────────────────────────

    @staticmethod
    def _fallback_decision(
        side: str,
        signals: Dict[str, Any],
        regime: str,
        recent_winrate: Optional[float],
    ) -> Decision:
        """Conservative rule-based decision when no LLM is available."""
        active = sum(1 for v in signals.values() if v and v != 0)
        # In crisis, default to skip unless 4+ confirming signals
        if regime == "crisis" and active < 4:
            return Decision(
                action="skip", side=side, confidence=0.0, size_mult=0.5,
                rationale="Crisis regime + insufficient signal confluence",
                risks=["Drawdown risk"], provider="fallback",
            )
        if active < 2:
            return Decision(
                action="skip", side=side, confidence=0.3, size_mult=0.5,
                rationale="Too few confirming signals",
                risks=["Low conviction"], provider="fallback",
            )
        # Map win-rate to confidence
        wr = recent_winrate if recent_winrate is not None else 0.5
        conf = max(0.4, min(0.85, wr + active * 0.05))
        sm = 0.7 if wr < 0.45 else 1.0 if wr < 0.55 else 1.3
        return Decision(
            action="enter", side=side, confidence=conf, size_mult=sm,
            rationale=f"Rule-based: {active} signals, recent winrate {wr:.0%}",
            risks=[], provider="fallback",
        )


# ── Singleton ─────────────────────────────────────────────────────────────────

_BRAIN: Optional[LLMBrain] = None
_BRAIN_LOCK = threading.Lock()


def get_brain() -> LLMBrain:
    global _BRAIN
    with _BRAIN_LOCK:
        if _BRAIN is None:
            _BRAIN = LLMBrain()
        return _BRAIN
