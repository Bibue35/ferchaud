"""
Bot Manager — Start/stop trading per user from the web dashboard.
Each user can toggle their bot on/off and switch passive/aggressive mode.
"""
from __future__ import annotations
import threading, time, logging
from datetime import datetime
from typing import Dict

log = logging.getLogger("bot_manager")

# Tracks running bot threads per user_id
_running: Dict[int, threading.Event] = {}
_threads: Dict[int, threading.Thread] = {}
_modes:   Dict[int, str] = {}


def start_bot(user_id: int, mode: str = "passive") -> bool:
    """Start the trading bot for a user. Returns True if started."""
    if user_id in _running and _running[user_id].is_set():
        # Already running — just update mode
        set_mode(user_id, mode)
        return False

    stop_event = threading.Event()
    _running[user_id] = stop_event
    _modes[user_id]   = mode

    t = threading.Thread(target=_bot_loop, args=(user_id, stop_event, mode), daemon=True)
    _threads[user_id] = t
    t.start()
    log.info("Bot started for user %d (mode=%s)", user_id, mode)

    # Update DB
    _update_db(user_id, bot_enabled=True, mode=mode)
    return True


def stop_bot(user_id: int) -> None:
    """Stop the trading bot for a user."""
    if user_id in _running:
        _running[user_id].set()
        del _running[user_id]
    _update_db(user_id, bot_enabled=False)
    log.info("Bot stopped for user %d", user_id)


def set_mode(user_id: int, mode: str) -> None:
    """Switch mode without restarting."""
    _modes[user_id] = mode
    _update_db(user_id, mode=mode)
    # Tell the strategy instance to switch mode
    try:
        from strategies.aggressive_breakout import strategy_instance
        if strategy_instance:
            strategy_instance.set_mode(mode)
    except Exception:
        pass


def get_status(user_id: int) -> dict:
    running = user_id in _running and not _running[user_id].is_set()
    return {
        "running": running,
        "mode": _modes.get(user_id, "passive"),
    }


def _bot_loop(user_id: int, stop_event: threading.Event, mode: str) -> None:
    """Main bot loop for a user."""
    from strategies.aggressive_breakout import AggressiveBreakoutStrategy, strategy_instance
    import strategies.aggressive_breakout as ab_module

    try:
        # Import bot core
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.broker import Broker
        from core.portfolio import Portfolio
        from core.executor import Executor
        from core.risk import RiskEngine
        from data.feed import DataFeed

        broker    = Broker()
        portfolio = Portfolio(broker)
        feed      = DataFeed()
        risk      = RiskEngine(portfolio)
        executor  = Executor(broker, portfolio, risk)

        strategy = AggressiveBreakoutStrategy(
            feed=feed, portfolio=portfolio,
            executor=executor, risk=risk, mode=mode
        )
        ab_module.strategy_instance = strategy

        log.info("Bot loop running for user %d | mode=%s", user_id, mode)

        while not stop_event.is_set():
            try:
                # Check if mode changed
                current_mode = _modes.get(user_id, mode)
                if current_mode != strategy.mode:
                    strategy.set_mode(current_mode)

                strategy.run()
            except Exception as e:
                log.error("Bot loop error user %d: %s", user_id, e)

            # Sleep between runs — passive waits longer
            interval = 300 if strategy.mode == "passive" else 180  # 5min or 3min
            for _ in range(interval):
                if stop_event.is_set():
                    break
                time.sleep(1)

    except Exception as e:
        log.error("Bot failed to start for user %d: %s", user_id, e)
    finally:
        _running.pop(user_id, None)
        log.info("Bot loop exited for user %d", user_id)


def _update_db(user_id: int, bot_enabled: bool | None = None, mode: str | None = None) -> None:
    try:
        from web.database import SessionLocal, User
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == user_id).first()
            if u:
                if bot_enabled is not None:
                    u.bot_enabled = bot_enabled
                    u.bot_started_at = datetime.utcnow() if bot_enabled else None
                if mode is not None:
                    u.trading_mode = mode
                db.commit()
        finally:
            db.close()
    except Exception as e:
        log.error("DB update failed: %s", e)
