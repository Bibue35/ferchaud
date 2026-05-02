#!/usr/bin/env python3
"""
QuantBot v2 — Autonomous Multi-Strategy Trading Engine (TURBO)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs strategies in parallel. 10-second loop. Aggressive execution.
"""
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from config import CONFIG
from utils.logger import get_logger

log = get_logger("main")

# ── Core imports ──────────────────────────────────────────────────────────────
from core.broker import Broker
from core.executor import OrderExecutor
from core.portfolio import Portfolio
from core.risk import RiskEngine
from core.regime import RegimeDetector
from core.vpin import VPINMonitor
from data.feed import MarketDataFeed
from data.sentiment import SentimentAnalyzer

# ── Strategy imports ──────────────────────────────────────────────────────────
from strategies.base import BaseStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.stat_arb import StatArbStrategy
from strategies.market_making import MarketMakingStrategy
from strategies.multi_factor import MultiFactorStrategy
from strategies.scalper import ScalperStrategy
from strategies.catalyst import CatalystStrategy
from strategies.catalyst import CatalystStrategy
from strategies.options_catalyst import OptionsCatalystStrategy
from strategies.gap_short import GapShortStrategy
from strategies.predictive_short import PredictiveShortStrategy
from strategies.aggressive_breakout import AggressiveBreakoutStrategy
from data.x_research import x_researcher
from data.full_market_scanner import scanner
from core.learning import get_engine as get_learning_engine
from core.exit_manager import ExitManager

# ── Timing (TURBO) ───────────────────────────────────────────────────────────
LOOP_INTERVAL_SECONDS = 10       # Was 60 — now ultra-fast
PORTFOLIO_LOG_INTERVAL = 5       # Log every 5th iteration
REGIME_CHECK_INTERVAL = 3        # Check regime every 3rd iteration
VPIN_REFRESH_INTERVAL = 2        # Refresh VPIN every 2nd iteration
LEARN_ADAPT_INTERVAL = 30        # Run adaptive tuner every 30 iterations (~5 min)
SNAPSHOT_INTERVAL    = 60        # Write portfolio snapshot every 60 iterations (~10 min)
MAX_WORKERS = 6                  # Thread pool size for parallel strategies

# Optional heartbeat hook (set by cloud_start.py supervisor)
__heartbeat__ = None


def _save_portfolio_snapshot(equity: float, cash: float, positions: dict) -> None:
    """Append a portfolio snapshot to the DB so the dashboard chart is populated.

    Best-effort: silently skips if the DB is unavailable (e.g. no schema yet).
    """
    try:
        import json as _json
        from web.database import SessionLocal, Portfolio, User
        db = SessionLocal()
        try:
            users = db.query(User).all()
            for u in users:
                snap = Portfolio(
                    user_id=u.id,
                    value=float(equity or 0),
                    cash=float(cash or 0),
                    positions=_json.dumps({
                        s: {
                            "qty": p.get("qty", 0),
                            "side": p.get("side", ""),
                            "market_value": p.get("market_value", 0),
                        }
                        for s, p in (positions or {}).items()
                    }),
                )
                db.add(snap)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def build_strategies(
    feed: MarketDataFeed,
    executor: OrderExecutor,
    portfolio: Portfolio,
    risk: RiskEngine,
    regime: Optional[RegimeDetector],
    vpin: Optional[VPINMonitor],
) -> List[BaseStrategy]:
    """Instantiate all enabled strategies."""
    strategies: List[BaseStrategy] = []
    args = (feed, executor, portfolio, risk, regime, vpin)

    if CONFIG.use_mean_reversion:
        strategies.append(MeanReversionStrategy(*args))
        log.info("  + Mean Reversion")

    if CONFIG.use_momentum:
        strategies.append(MomentumStrategy(*args))
        log.info("  + Momentum / Trend")

    if CONFIG.use_stat_arb and CONFIG.stat_arb_pairs:
        strategies.append(StatArbStrategy(*args))
        log.info("  + Statistical Arbitrage (%d pairs)", len(CONFIG.stat_arb_pairs))

    if CONFIG.use_market_making:
        strategies.append(MarketMakingStrategy(*args))
        log.info("  + Market Making")

    if CONFIG.use_multi_factor:
        strategies.append(MultiFactorStrategy(*args))
        log.info("  + Multi-Factor ML Alpha")

    # NEW: Aggressive scalper
    if getattr(CONFIG, 'use_scalper', True):
        strategies.append(ScalperStrategy(*args))
        log.info("  + Aggressive Scalper")

    # NEW: Catalyst / news-driven (scans 150+ stocks)
    if getattr(CONFIG, 'use_catalyst', True):
        strategies.append(CatalystStrategy(*args))
        log.info("  + Catalyst / News Scanner (150+ symbols)")

    # Options catalyst — calls/puts on insider buys + catalyst confluence
    strategies.append(OptionsCatalystStrategy(*args))
    log.info("  + Options Catalyst (insider buys + calls/puts)")

    # Gap/mover short — scans ALL stocks for big daily movers
    strategies.append(GapShortStrategy(*args))
    log.info("  + Gap/Mover Short (full market scan, down 5%%+ shorts)")

    # Predictive short — 3-layer: research → confirm → execute before price drops
    strategies.append(PredictiveShortStrategy(*args))
    log.info("  + Predictive Short (RSI divergence + distribution + timing)")

    # Aggressive Breakout — X research + breakout momentum + dynamic universe
    if getattr(CONFIG, 'use_aggressive_breakout', True):
        strategies.append(AggressiveBreakoutStrategy(*args))
        log.info("  + Aggressive Breakout (X research + breakout + %s)", 
                 "Grok AI" if CONFIG.xai_api_key else "API screeners only")

    return strategies


def print_banner() -> None:
    log.info("=" * 62)
    log.info("  QuantBot v2 TURBO — Autonomous Trading Engine")
    log.info("  Mode:       %s", CONFIG.trading_mode.upper())
    log.info("  Regime:     %s", "ON" if CONFIG.regime_enabled else "OFF")
    log.info("  VPIN:       %s", "ON" if CONFIG.vpin_enabled else "OFF")
    log.info("  Vol Target: %.0f%%", CONFIG.vol_target * 100)
    log.info("  Max DD:     %.0f%%", CONFIG.max_drawdown * 100)
    log.info("  Kelly Frac: %.0f%%", CONFIG.kelly_fraction * 100)
    log.info("  Max Lev:    %.1fx", CONFIG.max_leverage)
    log.info("  Order Type: %s", CONFIG.order_type)
    log.info("  Loop:       %ds", LOOP_INTERVAL_SECONDS)
    log.info("  Symbols:    %d stocks + %d crypto",
             len(CONFIG.stock_universe), len(CONFIG.crypto_universe))
    log.info("=" * 62)


def init_regime(feed: MarketDataFeed) -> Optional[RegimeDetector]:
    if not CONFIG.regime_enabled:
        return None
    try:
        detector = RegimeDetector(feed)
        if detector.fit():
            log.info("Regime: %s (scale=%.1f)", detector.state_name, detector.position_scale)
            return detector
        log.warning("Regime detector failed to fit — running without regime")
        return None
    except Exception as e:
        log.error("Regime init error: %s — running without regime", e)
        return None


def init_vpin(feed: MarketDataFeed, symbols: List[str]) -> Optional[VPINMonitor]:
    if not CONFIG.vpin_enabled:
        return None
    try:
        monitor = VPINMonitor(feed)
        for sym in symbols:
            try:
                monitor.initialize(sym)
            except Exception:
                pass
        return monitor
    except Exception as e:
        log.error("VPIN init error: %s — running without VPIN", e)
        return None


def run_strategy_safe(strategy: BaseStrategy) -> str:
    """Run a single strategy, catching all exceptions. Returns status string."""
    try:
        strategy.run()
        return f"{strategy.name}: OK"
    except Exception as exc:
        log.error("[%s] run() error: %s", strategy.name, exc)
        return f"{strategy.name}: ERROR ({exc})"


def main() -> None:
    # Prevent indefinite hangs on slow/dropped Alpaca connections
    import socket
    socket.setdefaulttimeout(20)
    print_banner()

    # ── Validate config ──────────────────────────────────────────────────
    try:
        CONFIG.validate()
    except ValueError as e:
        log.critical("Config error: %s", e)
        sys.exit(1)

    # ── Core components ──────────────────────────────────────────────────
    broker = Broker()
    feed = MarketDataFeed()
    portfolio = Portfolio(broker)
    risk = RiskEngine(portfolio.equity)
    executor = OrderExecutor(broker, risk, portfolio)

    # Cancel stale orders
    try:
        stale = broker.list_open_orders()
        if stale:
            broker._api.cancel_all_orders()
            log.info("Cancelled %d stale orders from previous run", len(stale))
    except Exception:
        pass

    # Sentiment
    try:
        sentiment = SentimentAnalyzer(broker._api)
        log.info("Sentiment analyzer: ON")
    except Exception as e:
        sentiment = None
        log.warning("Sentiment analyzer failed: %s", e)

    # Regime
    regime = init_regime(feed)

    # Collect all symbols
    all_symbols = list(set(CONFIG.stock_universe + CONFIG.crypto_universe))

    # VPIN
    vpin = init_vpin(feed, all_symbols)

    # Build strategies
    strategies = build_strategies(feed, executor, portfolio, risk, regime, vpin)
    if not strategies:
        log.critical("No strategies enabled — check .env")
        sys.exit(1)

    log.info("Strategies active: %d", len(strategies))
    strat_names = [s.name for s in strategies]

    # Start data stream
    strat_symbols = list({s for strat in strategies for s in strat.get_symbols()})
    feed.start_stream(strat_symbols)

    # Graceful shutdown
    _running = True

    def _shutdown(signum, frame):
        nonlocal _running
        log.warning("Shutdown signal received...")
        _running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Initial state
    portfolio.print_summary()
    if regime:
        log.info("Current regime: %s", regime.state_name)
    log.info("Bot running TURBO mode (%ds loop). Press Ctrl+C to stop.\n", LOOP_INTERVAL_SECONDS)

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN LOOP — TURBO
    # ══════════════════════════════════════════════════════════════════════
    iteration = 0
    executor_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    # Initialize learning engine once
    learn_engine = None
    try:
        learn_engine = get_learning_engine()
        log.info("LearningEngine ready — DB: %s", learn_engine.store.path)
    except Exception as e:
        log.warning("LearningEngine init failed: %s", e)

    # Initialize exit manager
    exit_mgr = None
    try:
        exit_mgr = ExitManager(executor, portfolio, feed,
                               max_hold_hours=24.0, check_interval_sec=15)
        log.info("ExitManager ready (trail/time stops)")
    except Exception as e:
        log.warning("ExitManager init failed: %s", e)

    while _running:
        iteration += 1
        t0 = time.time()
        log.info("--- Iteration %d " + "-" * 45, iteration)
        executor.reset_cycle()   # reset per-cycle entry counter

        # Heartbeat for cloud supervisor
        try:
            if __heartbeat__ is not None:
                __heartbeat__()
        except Exception:
            pass

        try:
            # 0. Full market scan (12,000+ stocks) + X research
            if scanner.needs_refresh():
                scanner.scan_async()
                hot = scanner.hot_symbols
                if hot:
                    log.info("SCANNER: %d hot stocks — top: %s", len(hot), ", ".join(hot[:8]))
            if x_researcher.needs_refresh():
                x_researcher.scan_async()

            # 1. Refresh portfolio and risk
            portfolio.refresh()
            risk.update_portfolio_value(portfolio.equity)

            # 1b. Detect closed positions → push exits to learning engine
            try:
                executor.detect_closures()
            except Exception:
                pass

            # 1c. Run adaptive exit manager (trail / time stops)
            if exit_mgr:
                try:
                    n_exits = exit_mgr.check_all()
                    if n_exits:
                        log.info("ExitManager triggered %d exits", n_exits)
                except Exception as e:
                    log.debug("ExitManager error: %s", e)

            # 2. Regime
            if regime and iteration % REGIME_CHECK_INTERVAL == 0:
                if regime.needs_retrain():
                    log.info("Retraining regime model...")
                    regime.fit()
                else:
                    regime.update()
                log.info("Regime: %s (scale=%.1f)", regime.state_name, regime.position_scale)

            # 3. VPIN refresh (parallel per symbol)
            if vpin and iteration % VPIN_REFRESH_INTERVAL == 0:
                for sym in strat_symbols:
                    try:
                        vpin.refresh(sym)
                    except Exception:
                        pass

            # 4. Strategy weights
            weights = risk.compute_strategy_weights(strat_names)
            if iteration % PORTFOLIO_LOG_INTERVAL == 0 and weights:
                log.info("Strategy weights: %s",
                         "  ".join(f"{k}={v:.1%}" for k, v in weights.items()))

            # 5. RUN ALL STRATEGIES IN PARALLEL
            if risk.is_halted:
                log.critical("DRAWDOWN LIMIT HIT -- no new trades this cycle")
            else:
                futures = {
                    executor_pool.submit(run_strategy_safe, strat): strat
                    for strat in strategies
                }
                for future in as_completed(futures, timeout=90):
                    try:
                        result = future.result()
                        log.debug("Strategy result: %s", result)
                    except Exception as exc:
                        strat = futures[future]
                        log.error("[%s] thread error: %s", strat.name, exc)

            # 6. Sentiment snapshot
            if sentiment and iteration % PORTFOLIO_LOG_INTERVAL == 0:
                try:
                    top_syms = CONFIG.stock_universe[:5]
                    scores = sentiment.get_bulk_sentiment(top_syms)
                    parts = [f"{s}={v:+.2f}" for s, v in scores.items() if v != 0]
                    if parts:
                        log.info("Sentiment: %s", "  ".join(parts))
                except Exception:
                    pass

            # 6b. Persist portfolio snapshot (for dashboard chart)
            if iteration % SNAPSHOT_INTERVAL == 0:
                try:
                    _save_portfolio_snapshot(portfolio.equity, portfolio.cash,
                                             portfolio.positions)
                except Exception:
                    pass

            # 7. Adaptive learning pass (every N iterations)
            if learn_engine and iteration % LEARN_ADAPT_INTERVAL == 0:
                try:
                    updates = learn_engine.adapt_all(strat_names)
                    changed = {k: v for k, v in updates.items() if v}
                    if changed:
                        for sname, params in list(changed.items())[:3]:
                            log.info("LEARN[%s] params=%s", sname,
                                     {k: round(v, 3) for k, v in params.items()})
                    summary = learn_engine.get_summary()
                    log.info("LEARN: open=%d arms=%d mistakes30d=%s",
                             summary.get("open_trades", 0),
                             summary.get("total_arms", 0),
                             summary.get("mistakes_30d", {}))
                except Exception as e:
                    log.warning("Learning adapt error: %s", e)

        except Exception as exc:
            log.error("Main loop error: %s", exc)

        # Portfolio summary
        if iteration % PORTFOLIO_LOG_INTERVAL == 0:
            try:
                portfolio.refresh()
                portfolio.print_summary()
            except Exception as e:
                log.error("Portfolio summary error: %s", e)

        elapsed = time.time() - t0
        log.info("Cycle completed in %.1fs", elapsed)

        # Sleep until next cycle (if we went over time, still sleep 1s minimum)
        sleep_time = max(1, LOOP_INTERVAL_SECONDS - elapsed)
        sleep_steps = int(sleep_time)
        for _ in range(sleep_steps):
            if not _running:
                log.warning("Loop exiting: _running=False during sleep step")
                break
            time.sleep(1)
        else:
            log.debug("Sleep complete, continuing to next iteration")
        if not _running:
            log.warning("Loop exiting after sleep: _running=False")

    # ══════════════════════════════════════════════════════════════════════
    #  SHUTDOWN
    # ══════════════════════════════════════════════════════════════════════
    executor_pool.shutdown(wait=False)
    log.info("Stopping data stream...")
    feed.stop_stream()
    portfolio.refresh()
    portfolio.print_summary()
    log.info("QuantBot shut down cleanly.")


if __name__ == "__main__":
    main()
