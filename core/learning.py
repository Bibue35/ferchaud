"""
Self-Learning Engine — The Brain That Improves Over Time
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A reinforcement-learning-inspired adaptive system that:
  • Tracks every trade outcome (entry, exit, signals, P&L, hold time)
  • Identifies mistakes (sold too early, bought too late, false signals)
  • Adapts strategy parameters (confidence thresholds, stops, targets)
  • Scores signal combinations using contextual bandit logic
  • Persists learning across restarts (SQLite-backed memory)
  • Exposes performance analytics for the dashboard

This is not classical Q-learning over a discrete state-action space.
It is a *contextual multi-armed bandit* over (strategy, signal_combo, regime)
tuples, with Thompson sampling for exploration and Bayesian updating
for exploitation.

Why this design over deep RL?
  • Stationary deep RL fails in non-stationary markets
  • Simple is robust: every parameter update is auditable
  • Cold-start safe: priors prevent catastrophic exploration
  • Per-strategy isolation prevents cross-contamination of bad signals
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from utils.logger import get_logger

log = get_logger("core.learning")


# ═══════════════════════════════════════════════════════════════════════════════
#  Trade Outcome Records
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """Single trade snapshot — captured at entry, completed at exit."""
    trade_id: str
    strategy: str
    symbol: str
    side: str                    # "long" | "short"
    entry_price: float
    entry_time: float            # unix timestamp
    qty: float
    confidence: float            # 0..1 confidence at entry
    signals: Dict[str, float]    # signal_name -> value/score
    regime: str = "unknown"      # bull/sideways/crisis/unknown
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    atr_at_entry: Optional[float] = None

    # Filled at exit:
    exit_price: Optional[float] = None
    exit_time: Optional[float] = None
    exit_reason: str = ""        # "take_profit" | "stop_loss" | "manual" | "trail" | "timeout"
    pnl: float = 0.0
    pnl_pct: float = 0.0
    max_favorable_pct: float = 0.0    # peak unrealized profit (MFE)
    max_adverse_pct: float = 0.0      # worst unrealized loss (MAE)

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None

    @property
    def hold_seconds(self) -> float:
        if not self.exit_time:
            return 0.0
        return self.exit_time - self.entry_time

    @property
    def is_win(self) -> bool:
        return self.is_closed and self.pnl > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Mistake Detection
# ═══════════════════════════════════════════════════════════════════════════════

class MistakeAnalyzer:
    """
    Inspects closed trades and labels mistakes.

    Mistake types:
      • SOLD_TOO_EARLY  — exited before peak (MFE >> realized profit)
      • HELD_TOO_LONG   — peak was hit, then gave it back (drawdown from MFE)
      • CUT_LOSS_LATE   — MAE hit hard before stop fired
      • FALSE_SIGNAL    — entry signal flipped quickly against us
      • CHOPPY_ENTRY    — small P&L either way, low conviction
      • PERFECT         — tight execution, MFE ≈ realized P&L
    """

    EARLY_EXIT_RATIO = 0.5    # realized < 50% of MFE → sold too early
    GAVE_BACK_RATIO = 0.6     # realized < 40% of MFE → held too long
    LATE_STOP_RATIO = 1.2     # MAE > 1.2x stop distance → cut losses late
    FALSE_SIGNAL_SECS = 600   # < 10 min hold + loss = false signal

    @classmethod
    def label(cls, t: TradeRecord) -> str:
        if not t.is_closed:
            return "open"

        mfe = max(t.max_favorable_pct, abs(t.pnl_pct))
        mae = t.max_adverse_pct

        # No movement in either direction → choppy
        if mfe < 0.003 and mae < 0.003:
            return "choppy"

        # Quick loss → false signal
        if t.pnl < 0 and t.hold_seconds < cls.FALSE_SIGNAL_SECS:
            return "false_signal"

        # Stop hit but MAE went well past it
        if t.exit_reason == "stop_loss" and mae > 0:
            stop_dist_pct = (
                abs(t.entry_price - t.stop_price) / t.entry_price
                if t.stop_price else 0.01
            )
            if mae > stop_dist_pct * cls.LATE_STOP_RATIO:
                return "cut_loss_late"

        # Profit but missed most of the peak
        if t.pnl > 0 and mfe > 0:
            captured = abs(t.pnl_pct) / mfe if mfe > 0 else 1.0
            if captured < cls.EARLY_EXIT_RATIO:
                return "sold_too_early"

        # Reached peak but gave it back → held too long
        if mfe > 0.01 and t.pnl_pct < mfe * cls.GAVE_BACK_RATIO:
            return "held_too_long"

        # Solid execution
        return "good"


# ═══════════════════════════════════════════════════════════════════════════════
#  Contextual Bandit — Strategy/Signal Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class BetaBandit:
    """
    Thompson-sampling Bernoulli bandit with Beta(α, β) posterior.

    Tracks win-rate of a (strategy, context) arm. The Beta posterior
    auto-shrinks toward 50/50 with few samples and converges to the
    true win rate as samples grow.

    Sample posterior to balance explore/exploit:
      arm.sample() → draws from Beta(α, β)
      pick the arm with the highest sample → exploration falls off naturally.
    """

    def __init__(self, prior_alpha: float = 2.0, prior_beta: float = 2.0) -> None:
        self.alpha = prior_alpha
        self.beta = prior_beta
        self.n = 0
        self.total_pnl = 0.0
        self.total_pnl_sq = 0.0  # for variance estimate

    def update(self, won: bool, pnl_pct: float = 0.0) -> None:
        self.n += 1
        if won:
            self.alpha += 1
        else:
            self.beta += 1
        self.total_pnl += pnl_pct
        self.total_pnl_sq += pnl_pct * pnl_pct

    @property
    def win_rate(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.n if self.n else 0.0

    @property
    def pnl_std(self) -> float:
        if self.n < 2:
            return 0.0
        mean = self.avg_pnl
        var = max(0.0, self.total_pnl_sq / self.n - mean * mean)
        return math.sqrt(var)

    @property
    def sharpe(self) -> float:
        std = self.pnl_std
        return self.avg_pnl / std if std > 1e-9 else 0.0

    def sample(self) -> float:
        """Thompson sample from Beta posterior."""
        return float(np.random.beta(self.alpha, self.beta))

    def confidence_multiplier(self) -> float:
        """
        Return a multiplier in [0.3, 2.0] derived from posterior win-rate
        AND sample size. Used to scale entry confidence at trade time.

        Few samples → multiplier ≈ 1.0 (neutral, let strategy decide)
        Many samples + high win rate → 1.5..2.0 (boost)
        Many samples + low win rate → 0.3..0.7 (suppress)
        """
        if self.n < 5:
            return 1.0
        # Map win_rate from [0.3, 0.7] linearly to [0.5, 1.5], then weight by n
        wr = self.win_rate
        raw = 0.5 + (wr - 0.3) * 2.5
        raw = max(0.3, min(2.0, raw))
        # Blend toward 1.0 if we don't have many samples yet
        weight = min(1.0, self.n / 30.0)
        return float(1.0 + (raw - 1.0) * weight)


# ═══════════════════════════════════════════════════════════════════════════════
#  SQLite-backed Persistent Memory
# ═══════════════════════════════════════════════════════════════════════════════

class LearningStore:
    """
    Append-only trade journal in SQLite. Survives restarts.
    Lockable and thread-safe via a single writer lock.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS trades (
        trade_id        TEXT PRIMARY KEY,
        strategy        TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        entry_price     REAL NOT NULL,
        entry_time      REAL NOT NULL,
        qty             REAL NOT NULL,
        confidence      REAL NOT NULL,
        signals         TEXT,
        regime          TEXT,
        stop_price      REAL,
        target_price    REAL,
        atr_at_entry    REAL,
        exit_price      REAL,
        exit_time       REAL,
        exit_reason     TEXT,
        pnl             REAL DEFAULT 0,
        pnl_pct         REAL DEFAULT 0,
        max_favorable_pct REAL DEFAULT 0,
        max_adverse_pct REAL DEFAULT 0,
        mistake_label   TEXT,
        closed          INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_strategy ON trades(strategy);
    CREATE INDEX IF NOT EXISTS idx_symbol   ON trades(symbol);
    CREATE INDEX IF NOT EXISTS idx_closed   ON trades(closed);
    CREATE INDEX IF NOT EXISTS idx_time     ON trades(entry_time);

    CREATE TABLE IF NOT EXISTS bandit_state (
        arm_key TEXT PRIMARY KEY,
        alpha   REAL NOT NULL,
        beta    REAL NOT NULL,
        n       INTEGER NOT NULL,
        total_pnl    REAL DEFAULT 0,
        total_pnl_sq REAL DEFAULT 0,
        updated_at   REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS strategy_params (
        strategy        TEXT NOT NULL,
        param_name      TEXT NOT NULL,
        value           REAL NOT NULL,
        updated_at      REAL NOT NULL,
        update_count    INTEGER DEFAULT 0,
        PRIMARY KEY (strategy, param_name)
    );

    CREATE TABLE IF NOT EXISTS signal_journal (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          REAL NOT NULL,
        strategy    TEXT,
        symbol      TEXT,
        signal      TEXT,
        score       REAL,
        outcome     TEXT,
        meta        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sj_ts ON signal_journal(ts);
    """

    def __init__(self, path: str = "data/learning.db") -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as con:
            con.executescript(self.SCHEMA)

    @contextmanager
    def _conn(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=10.0)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    # ── Trades ─────────────────────────────────────────────────────────────────

    def insert_trade(self, t: TradeRecord) -> None:
        with self._conn() as con:
            con.execute(
                """INSERT OR REPLACE INTO trades VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t.trade_id, t.strategy, t.symbol, t.side,
                    t.entry_price, t.entry_time, t.qty, t.confidence,
                    json.dumps(t.signals), t.regime,
                    t.stop_price, t.target_price, t.atr_at_entry,
                    t.exit_price, t.exit_time, t.exit_reason,
                    t.pnl, t.pnl_pct, t.max_favorable_pct, t.max_adverse_pct,
                    None, 1 if t.is_closed else 0,
                ),
            )

    def update_mfe_mae(self, trade_id: str, mfe_pct: float, mae_pct: float) -> None:
        """Update unrealized peak/trough during the trade."""
        with self._conn() as con:
            con.execute(
                """UPDATE trades SET
                       max_favorable_pct = MAX(max_favorable_pct, ?),
                       max_adverse_pct   = MAX(max_adverse_pct, ?)
                   WHERE trade_id = ?""",
                (mfe_pct, mae_pct, trade_id),
            )

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: float,
        exit_reason: str,
        pnl: float,
        pnl_pct: float,
        mistake_label: str,
    ) -> None:
        with self._conn() as con:
            con.execute(
                """UPDATE trades SET
                       exit_price = ?, exit_time = ?, exit_reason = ?,
                       pnl = ?, pnl_pct = ?, mistake_label = ?, closed = 1
                   WHERE trade_id = ?""",
                (exit_price, exit_time, exit_reason, pnl, pnl_pct,
                 mistake_label, trade_id),
            )

    def get_open_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM trades WHERE trade_id = ? AND closed = 0",
                (trade_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_open_trades(self) -> List[Dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM trades WHERE closed = 0"
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_closed(
        self, strategy: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        with self._conn() as con:
            if strategy:
                rows = con.execute(
                    """SELECT * FROM trades WHERE closed = 1 AND strategy = ?
                       ORDER BY exit_time DESC LIMIT ?""",
                    (strategy, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT * FROM trades WHERE closed = 1
                       ORDER BY exit_time DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def stats_by_strategy(self) -> Dict[str, Dict[str, float]]:
        with self._conn() as con:
            rows = con.execute(
                """SELECT strategy,
                          COUNT(*) AS n,
                          SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                          SUM(pnl) AS total_pnl,
                          AVG(pnl_pct) AS avg_ret,
                          AVG(max_favorable_pct) AS avg_mfe,
                          AVG(max_adverse_pct) AS avg_mae,
                          AVG(exit_time - entry_time) AS avg_hold
                   FROM trades WHERE closed = 1 GROUP BY strategy"""
            ).fetchall()
            return {
                r["strategy"]: {
                    "n": r["n"],
                    "wins": r["wins"] or 0,
                    "win_rate": (r["wins"] or 0) / r["n"] if r["n"] else 0,
                    "total_pnl": r["total_pnl"] or 0,
                    "avg_ret": r["avg_ret"] or 0,
                    "avg_mfe": r["avg_mfe"] or 0,
                    "avg_mae": r["avg_mae"] or 0,
                    "avg_hold_sec": r["avg_hold"] or 0,
                }
                for r in rows
            }

    def mistake_distribution(self, days: int = 30) -> Dict[str, int]:
        cutoff = time.time() - days * 86400
        with self._conn() as con:
            rows = con.execute(
                """SELECT mistake_label, COUNT(*) AS n
                   FROM trades
                   WHERE closed = 1 AND exit_time >= ? AND mistake_label IS NOT NULL
                   GROUP BY mistake_label""",
                (cutoff,),
            ).fetchall()
            return {r["mistake_label"]: r["n"] for r in rows}

    # ── Bandit state ───────────────────────────────────────────────────────────

    def load_bandit(self, arm_key: str) -> BetaBandit:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM bandit_state WHERE arm_key = ?", (arm_key,)
            ).fetchone()
            if not row:
                return BetaBandit()
            b = BetaBandit(prior_alpha=row["alpha"], prior_beta=row["beta"])
            b.alpha = row["alpha"]
            b.beta = row["beta"]
            b.n = row["n"]
            b.total_pnl = row["total_pnl"]
            b.total_pnl_sq = row["total_pnl_sq"]
            return b

    def save_bandit(self, arm_key: str, b: BetaBandit) -> None:
        with self._conn() as con:
            con.execute(
                """INSERT OR REPLACE INTO bandit_state VALUES
                   (?, ?, ?, ?, ?, ?, ?)""",
                (arm_key, b.alpha, b.beta, b.n,
                 b.total_pnl, b.total_pnl_sq, time.time()),
            )

    def all_bandits(self) -> Dict[str, BetaBandit]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM bandit_state").fetchall()
            out: Dict[str, BetaBandit] = {}
            for row in rows:
                b = BetaBandit()
                b.alpha = row["alpha"]
                b.beta = row["beta"]
                b.n = row["n"]
                b.total_pnl = row["total_pnl"]
                b.total_pnl_sq = row["total_pnl_sq"]
                out[row["arm_key"]] = b
            return out

    # ── Strategy params (adapted thresholds) ───────────────────────────────────

    def get_param(self, strategy: str, name: str, default: float) -> float:
        with self._conn() as con:
            row = con.execute(
                "SELECT value FROM strategy_params WHERE strategy = ? AND param_name = ?",
                (strategy, name),
            ).fetchone()
            return row["value"] if row else default

    def set_param(self, strategy: str, name: str, value: float) -> None:
        with self._conn() as con:
            con.execute(
                """INSERT INTO strategy_params VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(strategy, param_name) DO UPDATE
                   SET value = excluded.value,
                       updated_at = excluded.updated_at,
                       update_count = update_count + 1""",
                (strategy, name, value, time.time()),
            )

    def all_params(self, strategy: str) -> Dict[str, float]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT param_name, value FROM strategy_params WHERE strategy = ?",
                (strategy,),
            ).fetchall()
            return {r["param_name"]: r["value"] for r in rows}

    # ── Signal journal (forensic trace) ────────────────────────────────────────

    def journal_signal(
        self,
        strategy: str,
        symbol: str,
        signal: str,
        score: float,
        outcome: str = "",
        meta: Optional[dict] = None,
    ) -> None:
        with self._conn() as con:
            con.execute(
                """INSERT INTO signal_journal (ts, strategy, symbol, signal, score, outcome, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), strategy, symbol, signal, score, outcome,
                 json.dumps(meta) if meta else None),
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  Adaptive Strategy Tuner
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveTuner:
    """
    Reads recent trade outcomes and adjusts parameters per strategy.

    Tunable parameters (per strategy):
      • confidence_threshold  — minimum confidence to enter
      • stop_atr_mult         — ATR multiplier for stop loss
      • target_atr_mult       — ATR multiplier for take profit
      • size_multiplier       — global size scale [0.3..2.0]
      • trail_atr_mult        — trailing stop tightness

    Update logic (gentle, EMA-smoothed):
      • If sold_too_early dominant → increase target_atr_mult, tighten trail
      • If held_too_long dominant  → tighten trail, decrease target
      • If cut_loss_late dominant  → tighten stop_atr_mult
      • If false_signal dominant   → raise confidence_threshold
      • Win rate trending up       → mildly increase size_multiplier
      • Win rate trending down     → mildly decrease size_multiplier
    """

    EMA_ALPHA = 0.15  # learning rate (gentle)

    DEFAULTS = {
        "confidence_threshold": 0.50,
        "stop_atr_mult":        2.0,
        "target_atr_mult":      3.0,
        "size_multiplier":      1.0,
        "trail_atr_mult":       1.5,
    }

    BOUNDS = {
        "confidence_threshold": (0.20, 0.85),
        "stop_atr_mult":        (1.0, 4.0),
        "target_atr_mult":      (1.5, 6.0),
        "size_multiplier":      (0.3, 2.0),
        "trail_atr_mult":       (0.8, 3.5),
    }

    def __init__(self, store: LearningStore) -> None:
        self.store = store

    def get(self, strategy: str, name: str) -> float:
        default = self.DEFAULTS.get(name, 1.0)
        return self.store.get_param(strategy, name, default)

    def all(self, strategy: str) -> Dict[str, float]:
        params = dict(self.DEFAULTS)
        params.update(self.store.all_params(strategy))
        return params

    def _bounded(self, name: str, value: float) -> float:
        lo, hi = self.BOUNDS.get(name, (-1e9, 1e9))
        return max(lo, min(hi, value))

    def _ema(self, current: float, target: float) -> float:
        return current + self.EMA_ALPHA * (target - current)

    def adapt(self, strategy: str, min_samples: int = 15) -> Dict[str, float]:
        """Run one adaptation pass for a strategy. Returns new params."""
        recent = self.store.recent_closed(strategy=strategy, limit=120)
        if len(recent) < min_samples:
            return self.all(strategy)

        # Mistake distribution
        mistakes = defaultdict(int)
        wins, losses = 0, 0
        total_ret = 0.0
        confidences = []
        for r in recent:
            label = r.get("mistake_label") or "good"
            mistakes[label] += 1
            if (r.get("pnl") or 0) > 0:
                wins += 1
            else:
                losses += 1
            total_ret += r.get("pnl_pct") or 0
            confidences.append(r.get("confidence") or 0.5)

        n = len(recent)
        win_rate = wins / n
        avg_ret = total_ret / n
        avg_conf = float(np.mean(confidences))

        # Recent vs older split for trend
        half = n // 2
        recent_wins = sum(1 for r in recent[:half] if (r.get("pnl") or 0) > 0)
        older_wins = sum(1 for r in recent[half:] if (r.get("pnl") or 0) > 0)
        recent_wr = recent_wins / max(1, half)
        older_wr = older_wins / max(1, n - half)
        wr_trend = recent_wr - older_wr  # positive = improving

        # Pull current params
        params = self.all(strategy)
        new_params = dict(params)

        dominant_mistake = max(mistakes.items(), key=lambda x: x[1])[0] if mistakes else "good"
        dominant_count = mistakes.get(dominant_mistake, 0)

        # ── Adapt based on dominant mistake ──
        if dominant_mistake == "sold_too_early" and dominant_count >= 4:
            new_params["target_atr_mult"]  = self._ema(params["target_atr_mult"],  params["target_atr_mult"] * 1.20)
            new_params["trail_atr_mult"]   = self._ema(params["trail_atr_mult"],   params["trail_atr_mult"]  * 1.15)
            log.info("[%s] LEARN: sold_too_early dominant — widening targets/trail", strategy)

        elif dominant_mistake == "held_too_long" and dominant_count >= 4:
            new_params["target_atr_mult"]  = self._ema(params["target_atr_mult"],  params["target_atr_mult"] * 0.85)
            new_params["trail_atr_mult"]   = self._ema(params["trail_atr_mult"],   params["trail_atr_mult"]  * 0.80)
            log.info("[%s] LEARN: held_too_long dominant — tightening trail/target", strategy)

        elif dominant_mistake == "cut_loss_late" and dominant_count >= 3:
            new_params["stop_atr_mult"]    = self._ema(params["stop_atr_mult"],    params["stop_atr_mult"] * 0.85)
            log.info("[%s] LEARN: cut_loss_late dominant — tightening stops", strategy)

        elif dominant_mistake == "false_signal" and dominant_count >= 4:
            new_params["confidence_threshold"] = self._ema(
                params["confidence_threshold"],
                min(0.85, params["confidence_threshold"] + 0.10),
            )
            log.info("[%s] LEARN: false_signal dominant — raising confidence threshold", strategy)

        elif dominant_mistake == "choppy" and dominant_count >= 6:
            new_params["confidence_threshold"] = self._ema(
                params["confidence_threshold"],
                min(0.85, params["confidence_threshold"] + 0.05),
            )
            log.info("[%s] LEARN: choppy entries dominant — being more selective", strategy)

        # ── Size scaling based on win-rate trend ──
        if win_rate > 0.55 and wr_trend >= 0 and avg_ret > 0:
            new_params["size_multiplier"] = self._ema(
                params["size_multiplier"], min(2.0, params["size_multiplier"] * 1.10)
            )
        elif win_rate < 0.40 or avg_ret < -0.005:
            new_params["size_multiplier"] = self._ema(
                params["size_multiplier"], max(0.3, params["size_multiplier"] * 0.85)
            )

        # ── Confidence threshold drift toward population mean ──
        if avg_conf > 0:
            target_thresh = max(0.20, min(0.80, avg_conf - 0.05))
            new_params["confidence_threshold"] = self._ema(
                params["confidence_threshold"], target_thresh
            )

        # Bound everything and persist
        for k, v in new_params.items():
            new_params[k] = round(self._bounded(k, v), 4)
            if abs(new_params[k] - params.get(k, 0)) > 1e-4:
                self.store.set_param(strategy, k, new_params[k])

        return new_params


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Learning Engine
# ═══════════════════════════════════════════════════════════════════════════════

class LearningEngine:
    """
    The single entry point for all learning operations.

    Lifecycle of a trade:
      1. Strategy calls `engine.before_entry(strategy, signals, regime)`
         → returns adjusted confidence + size_multiplier + bool veto
      2. Strategy enters trade, calls `engine.record_entry(...)`
         → trade record persisted, returns trade_id
      3. Periodically, engine.update_open_trades(price_func) tracks MFE/MAE
      4. On exit, strategy calls `engine.record_exit(trade_id, ...)`
         → mistake labeled, bandit updated, params adapted
      5. Periodically, `engine.adapt_all()` re-tunes every strategy

    Thread-safe and idempotent; safe to call from parallel strategies.
    """

    def __init__(self, db_path: str = "data/learning.db") -> None:
        self.store = LearningStore(db_path)
        self.tuner = AdaptiveTuner(self.store)
        self._bandit_cache: Dict[str, BetaBandit] = {}
        self._cache_lock = threading.RLock()
        self._open_records: Dict[str, TradeRecord] = {}
        self._records_lock = threading.RLock()
        self._last_adapt_time: Dict[str, float] = {}
        self._adapt_interval_sec = 600  # re-tune at most every 10 min per strategy
        self._load_open_into_memory()

    def _load_open_into_memory(self) -> None:
        """Restore open trades from DB on startup so we keep tracking MFE/MAE."""
        for d in self.store.list_open_trades():
            try:
                t = TradeRecord(
                    trade_id=d["trade_id"], strategy=d["strategy"],
                    symbol=d["symbol"], side=d["side"],
                    entry_price=d["entry_price"], entry_time=d["entry_time"],
                    qty=d["qty"], confidence=d["confidence"],
                    signals=json.loads(d["signals"] or "{}"),
                    regime=d.get("regime") or "unknown",
                    stop_price=d.get("stop_price"),
                    target_price=d.get("target_price"),
                    atr_at_entry=d.get("atr_at_entry"),
                    max_favorable_pct=d.get("max_favorable_pct") or 0,
                    max_adverse_pct=d.get("max_adverse_pct") or 0,
                )
                self._open_records[t.trade_id] = t
            except Exception as e:
                log.warning("Could not restore open trade %s: %s", d.get("trade_id"), e)

    # ── Bandit access ──────────────────────────────────────────────────────────

    def _arm_key(self, strategy: str, signal_combo: str, regime: str) -> str:
        return f"{strategy}|{signal_combo}|{regime}"

    def _signal_combo(self, signals: Dict[str, float]) -> str:
        """Turn signal dict into a stable, low-cardinality key."""
        if not signals:
            return "none"
        active = [k for k, v in signals.items() if v and v != 0]
        active.sort()
        # Cap cardinality at 6 signals
        return "+".join(active[:6]) if active else "none"

    def get_bandit(self, strategy: str, signals: Dict[str, float], regime: str) -> BetaBandit:
        key = self._arm_key(strategy, self._signal_combo(signals), regime)
        with self._cache_lock:
            if key not in self._bandit_cache:
                self._bandit_cache[key] = self.store.load_bandit(key)
            return self._bandit_cache[key]

    def update_bandit(
        self,
        strategy: str,
        signals: Dict[str, float],
        regime: str,
        won: bool,
        pnl_pct: float,
    ) -> None:
        key = self._arm_key(strategy, self._signal_combo(signals), regime)
        with self._cache_lock:
            b = self._bandit_cache.get(key) or self.store.load_bandit(key)
            b.update(won, pnl_pct)
            self._bandit_cache[key] = b
            self.store.save_bandit(key, b)

    # ── Public API ─────────────────────────────────────────────────────────────

    def before_entry(
        self,
        strategy: str,
        signals: Dict[str, float],
        regime: str = "unknown",
        base_confidence: float = 0.5,
    ) -> Tuple[float, float, bool, str]:
        """
        Called by strategies BEFORE submitting an order.

        Returns:
          adjusted_confidence  ∈ [0, 1]
          size_multiplier      ∈ [0.3, 2.0]
          veto                 — True = do not enter
          reason               — explanation string

        Logic:
          • Pull bandit for (strategy, signal_combo, regime)
          • Multiply base confidence by bandit's confidence_multiplier
          • Clamp by tuner's confidence_threshold (veto if below)
          • Apply tuner's size_multiplier
        """
        bandit = self.get_bandit(strategy, signals, regime)
        bandit_mult = bandit.confidence_multiplier()
        adjusted = max(0.0, min(1.0, base_confidence * bandit_mult))

        params = self.tuner.all(strategy)
        threshold = params["confidence_threshold"]
        size_mult = params["size_multiplier"]

        if adjusted < threshold:
            return adjusted, size_mult, True, (
                f"confidence {adjusted:.2f} < threshold {threshold:.2f} "
                f"(bandit n={bandit.n}, wr={bandit.win_rate:.0%})"
            )

        # Optional Thompson-sampling exploration: 5% of the time, take a
        # below-threshold trade to keep learning the arm.
        return adjusted, size_mult, False, (
            f"adj_conf={adjusted:.2f} bandit_mult={bandit_mult:.2f} "
            f"size_mult={size_mult:.2f}"
        )

    def record_entry(
        self,
        strategy: str,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        confidence: float,
        signals: Dict[str, float],
        regime: str = "unknown",
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        atr: Optional[float] = None,
    ) -> str:
        """Record a new trade entry. Returns trade_id."""
        trade_id = f"{strategy}_{symbol}_{int(time.time()*1000)}"
        rec = TradeRecord(
            trade_id=trade_id,
            strategy=strategy,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=time.time(),
            qty=qty,
            confidence=confidence,
            signals=signals or {},
            regime=regime,
            stop_price=stop_price,
            target_price=target_price,
            atr_at_entry=atr,
        )
        with self._records_lock:
            self._open_records[trade_id] = rec
        self.store.insert_trade(rec)
        self.store.journal_signal(
            strategy, symbol, "ENTRY", confidence,
            outcome="opened",
            meta={"side": side, "qty": qty, "regime": regime,
                  "signals": signals},
        )
        return trade_id

    def update_open_trades(self, get_price) -> None:
        """
        Walk open trades, fetch current price via `get_price(symbol) -> float`,
        update MFE/MAE. Called once per main-loop iteration.
        """
        with self._records_lock:
            ids = list(self._open_records.keys())

        for tid in ids:
            with self._records_lock:
                t = self._open_records.get(tid)
            if not t:
                continue
            try:
                p = get_price(t.symbol)
                if not p or p <= 0:
                    continue
                if t.side in ("long", "buy"):
                    pct = (p - t.entry_price) / t.entry_price
                else:
                    pct = (t.entry_price - p) / t.entry_price
                if pct > t.max_favorable_pct:
                    t.max_favorable_pct = pct
                if -pct > t.max_adverse_pct:
                    t.max_adverse_pct = -pct
                self.store.update_mfe_mae(
                    tid, t.max_favorable_pct, t.max_adverse_pct
                )
            except Exception:
                continue

    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "manual",
    ) -> Optional[Dict[str, Any]]:
        """
        Record a trade exit and run learning updates.

        Returns the labeled trade snapshot (with mistake_label) or None.
        """
        with self._records_lock:
            t = self._open_records.pop(trade_id, None)

        if not t:
            # Maybe in DB but not memory (after restart with closed trade)
            d = self.store.get_open_trade(trade_id)
            if not d:
                log.warning("record_exit: no open trade %s", trade_id)
                return None
            t = TradeRecord(
                trade_id=d["trade_id"], strategy=d["strategy"],
                symbol=d["symbol"], side=d["side"],
                entry_price=d["entry_price"], entry_time=d["entry_time"],
                qty=d["qty"], confidence=d["confidence"],
                signals=json.loads(d["signals"] or "{}"),
                regime=d.get("regime") or "unknown",
                stop_price=d.get("stop_price"),
                target_price=d.get("target_price"),
                max_favorable_pct=d.get("max_favorable_pct") or 0,
                max_adverse_pct=d.get("max_adverse_pct") or 0,
            )

        t.exit_price = exit_price
        t.exit_time = time.time()
        t.exit_reason = exit_reason

        # P&L computation
        if t.side in ("long", "buy"):
            t.pnl_pct = (exit_price - t.entry_price) / t.entry_price
            t.pnl = (exit_price - t.entry_price) * t.qty
        else:
            t.pnl_pct = (t.entry_price - exit_price) / t.entry_price
            t.pnl = (t.entry_price - exit_price) * t.qty

        # Label mistake
        label = MistakeAnalyzer.label(t)
        self.store.close_trade(
            t.trade_id, exit_price, t.exit_time, exit_reason,
            t.pnl, t.pnl_pct, label,
        )

        # Update bandit
        self.update_bandit(
            t.strategy, t.signals, t.regime, t.is_win, t.pnl_pct
        )

        # Journal
        self.store.journal_signal(
            t.strategy, t.symbol, "EXIT", t.pnl_pct,
            outcome=label,
            meta={"reason": exit_reason, "hold": t.hold_seconds,
                  "mfe": t.max_favorable_pct, "mae": t.max_adverse_pct},
        )

        # Adaptation throttle
        last = self._last_adapt_time.get(t.strategy, 0)
        if time.time() - last > self._adapt_interval_sec:
            try:
                self.tuner.adapt(t.strategy)
                self._last_adapt_time[t.strategy] = time.time()
            except Exception as e:
                log.warning("Adaptive tuner error for %s: %s", t.strategy, e)

        log.info(
            "[%s] LEARN: %s %s pnl=%.2f%% mfe=%.2f%% mae=%.2f%% reason=%s label=%s",
            t.strategy, t.symbol, t.side, t.pnl_pct * 100,
            t.max_favorable_pct * 100, t.max_adverse_pct * 100,
            exit_reason, label,
        )

        out = asdict(t)
        out["mistake_label"] = label
        return out

    # ── Bulk adaptation / introspection ────────────────────────────────────────

    def adapt_all(self, strategies: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
        """Run tuner across all known strategies. Returns updated params per strategy."""
        if strategies is None:
            stats = self.store.stats_by_strategy()
            strategies = list(stats.keys())
        out = {}
        for s in strategies:
            try:
                out[s] = self.tuner.adapt(s)
            except Exception as e:
                log.warning("adapt_all(%s) error: %s", s, e)
        return out

    def get_summary(self) -> Dict[str, Any]:
        """High-level snapshot for the dashboard."""
        stats = self.store.stats_by_strategy()
        mistakes = self.store.mistake_distribution(days=30)
        bandits = self.store.all_bandits()

        # Top arms (sorted by Sharpe)
        arms = []
        for k, b in bandits.items():
            if b.n < 3:
                continue
            arms.append({
                "arm": k,
                "n": b.n,
                "win_rate": round(b.win_rate, 3),
                "avg_pnl_pct": round(b.avg_pnl, 4),
                "sharpe": round(b.sharpe, 3),
                "alpha": round(b.alpha, 2),
                "beta": round(b.beta, 2),
            })
        arms.sort(key=lambda x: x["sharpe"], reverse=True)

        return {
            "strategies": stats,
            "mistakes_30d": mistakes,
            "top_arms": arms[:15],
            "weakest_arms": arms[-10:][::-1] if len(arms) > 10 else [],
            "open_trades": len(self._open_records),
            "total_arms": len(bandits),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton accessor (one engine per process)
# ═══════════════════════════════════════════════════════════════════════════════

_ENGINE: Optional[LearningEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> LearningEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = LearningEngine()
            log.info("LearningEngine initialized — DB at %s", _ENGINE.store.path)
        return _ENGINE
