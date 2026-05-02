"""
Performance Analytics
━━━━━━━━━━━━━━━━━━━━━

Computes Sharpe, Sortino, Calmar, max drawdown, profit factor, etc. from
the learning engine's closed-trade journal. Used by the dashboard's
performance tab and the public /api/analytics endpoint.

All math is done with numpy on a list of (timestamp, pnl_pct) tuples
extracted from trades. Streams results lazily — no heavy joins.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


def _to_returns(closed_trades: List[dict]) -> np.ndarray:
    arr = np.array(
        [t.get("pnl_pct") or 0.0 for t in closed_trades if t.get("closed")],
        dtype=float,
    )
    return arr


def _max_drawdown(returns: np.ndarray) -> float:
    """Max drawdown of a cumulative-return curve. Returns positive number."""
    if len(returns) == 0:
        return 0.0
    cum = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cum)
    dd = (peak - cum) / peak
    return float(dd.max())


def compute_metrics(
    closed_trades: List[dict],
    risk_free_rate: float = 0.0,
    annualization: int = 252,
) -> Dict[str, float]:
    """
    Compute headline performance metrics from closed-trade list.
    closed_trades is the list returned by LearningStore.recent_closed().
    """
    if not closed_trades:
        return {
            "n_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "avg_pnl_pct": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
            "max_drawdown": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "avg_hold_min": 0.0,
        }

    rets = _to_returns(closed_trades)
    n = len(rets)
    wins = rets[rets > 0]
    losses = rets[rets < 0]

    win_rate = len(wins) / n if n else 0.0
    avg_ret = float(rets.mean())
    std_ret = float(rets.std(ddof=1)) if n > 1 else 0.0
    downside = rets[rets < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0

    sharpe = (avg_ret - risk_free_rate) / std_ret * math.sqrt(annualization) \
        if std_ret > 1e-9 else 0.0
    sortino = (avg_ret - risk_free_rate) / downside_std * math.sqrt(annualization) \
        if downside_std > 1e-9 else 0.0

    mdd = _max_drawdown(rets)
    annual_return = avg_ret * annualization
    calmar = annual_return / mdd if mdd > 1e-9 else 0.0

    pf_num = float(wins.sum())
    pf_den = abs(float(losses.sum())) if len(losses) else 0.0
    profit_factor = pf_num / pf_den if pf_den > 1e-9 else (pf_num * 1.0 if pf_num > 0 else 0.0)

    expectancy = avg_ret  # per-trade expected return
    avg_hold = float(np.mean([
        (t.get("exit_time", 0) - t.get("entry_time", 0)) / 60.0
        for t in closed_trades
    ])) if closed_trades else 0.0

    total_pnl = float(np.sum([t.get("pnl") or 0 for t in closed_trades]))

    return {
        "n_trades":      int(n),
        "win_rate":      round(win_rate, 4),
        "total_pnl":     round(total_pnl, 2),
        "avg_pnl_pct":   round(avg_ret * 100, 4),
        "best_trade":    round(float(rets.max()) * 100, 3) if n else 0.0,
        "worst_trade":   round(float(rets.min()) * 100, 3) if n else 0.0,
        "sharpe":        round(sharpe, 3),
        "sortino":       round(sortino, 3),
        "calmar":        round(calmar, 3),
        "max_drawdown":  round(mdd * 100, 3),
        "profit_factor": round(profit_factor, 3),
        "expectancy":    round(expectancy * 100, 4),
        "avg_hold_min":  round(avg_hold, 1),
    }


def equity_curve(closed_trades: List[dict], starting_equity: float = 10000.0) -> List[dict]:
    """
    Build a synthetic equity curve from sequential trade returns.
    Returns: [{"t": iso_ts, "v": equity}, ...]
    """
    if not closed_trades:
        return []
    sorted_trades = sorted(
        closed_trades, key=lambda t: t.get("exit_time", 0)
    )
    points = []
    equity = starting_equity
    for t in sorted_trades:
        equity = equity * (1 + (t.get("pnl_pct") or 0))
        ts = t.get("exit_time", time.time())
        points.append({
            "t": ts,
            "v": round(equity, 2),
        })
    return points


def per_strategy_metrics(closed_trades: List[dict]) -> Dict[str, Dict[str, float]]:
    """Group trades by strategy and compute metrics per strategy."""
    by_strat: Dict[str, List[dict]] = {}
    for t in closed_trades:
        s = t.get("strategy") or "unknown"
        by_strat.setdefault(s, []).append(t)
    return {s: compute_metrics(rows) for s, rows in by_strat.items()}
