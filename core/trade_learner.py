"""Trade Learner — Self-improving feedback loop.

After each trade closes (win or loss), the learner:
1. Records the trade outcome in a SQLite journal
2. Analyzes what signals were active at entry
3. Updates signal weights based on outcomes
4. Adjusts strategy parameters over time

The bot literally learns from its mistakes.
"""
import os
import json
import time
import sqlite3
import math
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trade_journal.db"


class TradeLearner:
    def __init__(self):
        self.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._create_tables()
        self.signal_weights = self._load_weights()
        self.min_trades_for_learning = 20  # Need at least 20 trades before adjusting
        self.learning_rate = 0.05  # How fast weights change (conservative)
        self.decay_factor = 0.95  # Recent trades matter more
        
    def _create_tables(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,  -- buy/sell
                entry_price REAL,
                exit_price REAL,
                qty REAL,
                pnl REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                strategy TEXT,
                signals_at_entry TEXT,  -- JSON: which signals were active
                signal_score INTEGER,
                market_regime TEXT,     -- bull/bear/sideways
                volatility REAL,
                volume_ratio REAL,
                entry_time TEXT,
                exit_time TEXT,
                hold_duration_min REAL,
                exit_reason TEXT,       -- stop_loss, take_profit, trailing_stop, time_stop, signal_flip
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS signal_weights (
                signal_name TEXT PRIMARY KEY,
                weight REAL DEFAULT 1.0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                avg_pnl_pct REAL DEFAULT 0,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS strategy_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                parameter TEXT,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS daily_performance (
                date TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                max_win REAL DEFAULT 0,
                max_loss REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_hold_min REAL DEFAULT 0
            );
        """)
        self.db.commit()
        
        # Initialize default signal weights if empty
        default_signals = [
            "ema_crossover", "ema_alignment", "macd_signal", "macd_histogram",
            "volume_spike", "volume_rank", "atr_expansion", "bb_squeeze",
            "bb_breakout", "rsi_oversold", "rsi_overbought", "rsi_momentum",
            "consolidation_breakout", "x_research", "scanner_score",
            "vpin_safe", "regime_bull", "regime_bear", "regime_sideways",
            "news_catalyst", "gap_up", "gap_down", "relative_strength"
        ]
        for sig in default_signals:
            self.db.execute(
                "INSERT OR IGNORE INTO signal_weights (signal_name, weight) VALUES (?, 1.0)",
                (sig,)
            )
        self.db.commit()
    
    def _load_weights(self) -> dict:
        """Load current signal weights from DB."""
        rows = self.db.execute("SELECT signal_name, weight FROM signal_weights").fetchall()
        return {name: weight for name, weight in rows}
    
    def record_trade(self, trade_data: dict):
        """Record a completed trade in the journal.
        
        trade_data should include:
            symbol, side, entry_price, exit_price, qty, pnl, strategy,
            signals_at_entry (list of signal names), signal_score,
            market_regime, volatility, volume_ratio, entry_time, exit_time,
            exit_reason
        """
        signals = trade_data.get("signals_at_entry", [])
        entry_time = trade_data.get("entry_time", datetime.utcnow().isoformat())
        exit_time = trade_data.get("exit_time", datetime.utcnow().isoformat())
        
        # Calculate hold duration
        try:
            entry_dt = datetime.fromisoformat(entry_time)
            exit_dt = datetime.fromisoformat(exit_time)
            hold_min = (exit_dt - entry_dt).total_seconds() / 60
        except:
            hold_min = 0
            
        # Calculate PnL percentage
        entry_price = trade_data.get("entry_price", 0)
        pnl_pct = 0
        if entry_price > 0:
            pnl_pct = (trade_data.get("pnl", 0) / (entry_price * trade_data.get("qty", 1))) * 100
        
        self.db.execute("""
            INSERT INTO trade_journal 
            (symbol, side, entry_price, exit_price, qty, pnl, pnl_pct,
             strategy, signals_at_entry, signal_score, market_regime,
             volatility, volume_ratio, entry_time, exit_time,
             hold_duration_min, exit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data.get("symbol", ""),
            trade_data.get("side", ""),
            entry_price,
            trade_data.get("exit_price", 0),
            trade_data.get("qty", 0),
            trade_data.get("pnl", 0),
            pnl_pct,
            trade_data.get("strategy", ""),
            json.dumps(signals),
            trade_data.get("signal_score", 0),
            trade_data.get("market_regime", "unknown"),
            trade_data.get("volatility", 0),
            trade_data.get("volume_ratio", 0),
            entry_time,
            exit_time,
            hold_min,
            trade_data.get("exit_reason", "unknown"),
        ))
        self.db.commit()
        
        # Learn from this trade
        self._update_signal_weights(signals, trade_data.get("pnl", 0), pnl_pct)
        self._update_daily_performance()
        self._check_strategy_adjustments(trade_data)
        
        print(f"[LEARNER] Recorded: {trade_data.get('symbol')} "
              f"{'WIN' if trade_data.get('pnl', 0) > 0 else 'LOSS'} "
              f"${trade_data.get('pnl', 0):.2f} | "
              f"Signals: {', '.join(signals[:3])}")
    
    def _update_signal_weights(self, signals: list, pnl: float, pnl_pct: float):
        """Update signal weights based on trade outcome.
        
        Winning trades → increase weight of active signals
        Losing trades → decrease weight of active signals
        """
        if not signals:
            return
            
        is_win = pnl > 0
        adjustment = self.learning_rate * (1 if is_win else -1)
        
        # Scale adjustment by magnitude of win/loss
        magnitude = min(abs(pnl_pct) / 5.0, 2.0)  # Cap at 2x
        adjustment *= magnitude
        
        for signal in signals:
            if signal not in self.signal_weights:
                self.signal_weights[signal] = 1.0
                
            old_weight = self.signal_weights[signal]
            new_weight = max(0.1, min(3.0, old_weight + adjustment))  # Clamp [0.1, 3.0]
            self.signal_weights[signal] = new_weight
            
            # Update DB
            if is_win:
                self.db.execute("""
                    UPDATE signal_weights 
                    SET weight = ?, win_count = win_count + 1,
                        avg_pnl_pct = (avg_pnl_pct * win_count + ?) / (win_count + 1),
                        last_updated = ?
                    WHERE signal_name = ?
                """, (new_weight, pnl_pct, datetime.utcnow().isoformat(), signal))
            else:
                self.db.execute("""
                    UPDATE signal_weights 
                    SET weight = ?, loss_count = loss_count + 1,
                        avg_pnl_pct = (avg_pnl_pct * loss_count + ?) / (loss_count + 1),
                        last_updated = ?
                    WHERE signal_name = ?
                """, (new_weight, pnl_pct, datetime.utcnow().isoformat(), signal))
        
        self.db.commit()
    
    def get_signal_weight(self, signal_name: str) -> float:
        """Get the current weight for a signal. Used by strategies to adjust scoring."""
        return self.signal_weights.get(signal_name, 1.0)
    
    def get_weighted_score(self, active_signals: list) -> float:
        """Calculate a weighted signal score based on learned weights.
        
        Instead of just counting signals (score = len(signals)),
        this weights each signal by its historical performance.
        """
        score = 0
        for signal in active_signals:
            score += self.get_signal_weight(signal)
        return round(score, 2)
    
    def _check_strategy_adjustments(self, trade_data: dict):
        """Check if strategy parameters should be adjusted.
        
        Analyzes recent performance and suggests/applies adjustments:
        - If stop losses are too tight (many stop-outs followed by price recovery) → widen
        - If hold times are too short (exiting before full move) → extend time stops
        - If a specific strategy has very low win rate → reduce position size
        - If a strategy is consistently profitable → allow more capital
        """
        strategy = trade_data.get("strategy", "")
        if not strategy:
            return
            
        # Get last 50 trades for this strategy
        rows = self.db.execute("""
            SELECT pnl, exit_reason, hold_duration_min, pnl_pct
            FROM trade_journal WHERE strategy = ?
            ORDER BY created_at DESC LIMIT 50
        """, (strategy,)).fetchall()
        
        if len(rows) < self.min_trades_for_learning:
            return
            
        wins = sum(1 for r in rows if r[0] > 0)
        losses = len(rows) - wins
        win_rate = wins / len(rows) if rows else 0
        
        # Count stop-outs
        stop_outs = sum(1 for r in rows if r[1] == "stop_loss")
        stop_out_rate = stop_outs / len(rows) if rows else 0
        
        # Average hold time for winners vs losers
        winner_holds = [r[2] for r in rows if r[0] > 0 and r[2] > 0]
        loser_holds = [r[2] for r in rows if r[0] <= 0 and r[2] > 0]
        avg_win_hold = sum(winner_holds) / len(winner_holds) if winner_holds else 0
        avg_loss_hold = sum(loser_holds) / len(loser_holds) if loser_holds else 0
        
        adjustments = []
        
        # If stop-out rate > 60%, stops might be too tight
        if stop_out_rate > 0.6 and len(rows) >= 30:
            adjustments.append({
                "parameter": "stop_loss_multiplier",
                "direction": "increase",
                "reason": f"Stop-out rate {stop_out_rate:.0%} — stops may be too tight"
            })
        
        # If win rate < 35%, strategy might need higher signal threshold
        if win_rate < 0.35 and len(rows) >= 30:
            adjustments.append({
                "parameter": "min_signal_score",
                "direction": "increase",
                "reason": f"Win rate {win_rate:.0%} — need stronger entry signals"
            })
        
        # If average winner hold time is much longer than loser, we're exiting losers too late
        if avg_loss_hold > avg_win_hold * 1.5 and avg_win_hold > 0:
            adjustments.append({
                "parameter": "time_stop_minutes",
                "direction": "decrease",
                "reason": f"Avg loss hold {avg_loss_hold:.0f}m > avg win hold {avg_win_hold:.0f}m — cut losers faster"
            })
        
        for adj in adjustments:
            self.db.execute("""
                INSERT INTO strategy_adjustments (strategy, parameter, old_value, new_value, reason)
                VALUES (?, ?, 0, 0, ?)
            """, (strategy, adj["parameter"], adj["reason"]))
            print(f"[LEARNER] Adjustment suggested for {strategy}: "
                  f"{adj['parameter']} → {adj['direction']} ({adj['reason']})")
        
        if adjustments:
            self.db.commit()
    
    def _update_daily_performance(self):
        """Update daily performance summary."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        rows = self.db.execute("""
            SELECT pnl, hold_duration_min FROM trade_journal
            WHERE date(created_at) = ?
        """, (today,)).fetchall()
        
        if not rows:
            return
            
        total = len(rows)
        wins = sum(1 for r in rows if r[0] > 0)
        losses = total - wins
        total_pnl = sum(r[0] for r in rows)
        avg_pnl = total_pnl / total if total > 0 else 0
        max_win = max((r[0] for r in rows), default=0)
        max_loss = min((r[0] for r in rows), default=0)
        win_rate = wins / total if total > 0 else 0
        avg_hold = sum(r[1] for r in rows if r[1]) / total if total > 0 else 0
        
        self.db.execute("""
            INSERT OR REPLACE INTO daily_performance
            (date, total_trades, wins, losses, total_pnl, avg_pnl,
             max_win, max_loss, win_rate, avg_hold_min)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, total, wins, losses, total_pnl, avg_pnl,
              max_win, max_loss, win_rate, avg_hold))
        self.db.commit()
    
    def get_performance_summary(self, days: int = 30) -> dict:
        """Get performance summary for the last N days."""
        rows = self.db.execute("""
            SELECT * FROM daily_performance
            ORDER BY date DESC LIMIT ?
        """, (days,)).fetchall()
        
        if not rows:
            return {"message": "No trade data yet"}
            
        total_trades = sum(r[1] for r in rows)
        total_wins = sum(r[2] for r in rows)
        total_pnl = sum(r[4] for r in rows)
        
        return {
            "days": len(rows),
            "total_trades": total_trades,
            "total_wins": total_wins,
            "win_rate": total_wins / total_trades if total_trades > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "best_day": max(rows, key=lambda r: r[4])[0] if rows else None,
            "worst_day": min(rows, key=lambda r: r[4])[0] if rows else None,
            "top_signals": self._get_top_signals(),
            "worst_signals": self._get_worst_signals(),
        }
    
    def _get_top_signals(self, n: int = 5) -> list:
        """Get the highest-weighted signals (most reliable)."""
        rows = self.db.execute("""
            SELECT signal_name, weight, win_count, loss_count
            FROM signal_weights
            WHERE (win_count + loss_count) >= 5
            ORDER BY weight DESC LIMIT ?
        """, (n,)).fetchall()
        return [{"signal": r[0], "weight": round(r[1], 2), 
                 "wins": r[2], "losses": r[3]} for r in rows]
    
    def _get_worst_signals(self, n: int = 5) -> list:
        """Get the lowest-weighted signals (least reliable)."""
        rows = self.db.execute("""
            SELECT signal_name, weight, win_count, loss_count
            FROM signal_weights
            WHERE (win_count + loss_count) >= 5
            ORDER BY weight ASC LIMIT ?
        """, (n,)).fetchall()
        return [{"signal": r[0], "weight": round(r[1], 2),
                 "wins": r[2], "losses": r[3]} for r in rows]


# Singleton
trade_learner = TradeLearner()
