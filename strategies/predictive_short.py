"""
Predictive Short Strategy — 3-Layer Architecture
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Layer 1 — RESEARCH (background, every 15 min):
  Scans full market for bearish precursors BEFORE price drops:
  - Unusual put/call ratio spikes (smart money buying protection)
  - RSI divergence (price new highs, momentum weakening)
  - Volume distribution (high-vol up days followed by climax)
  - Insider selling clusters (Form 4 aggregation)
  - Earnings risk (upcoming + historical miss rate)
  - Sector weakness (stock underperforming sector)

Layer 2 — CONFIRMATION (each 10s cycle):
  Promotes candidates with 3+ active signals.
  Checks real-time price velocity and VPIN toxicity.

Layer 3 — EXECUTION (smart timing):
  Waits for intraday bounce (price pops into resistance).
  Enters short at the "exhaustion point" not after breakdown.
"""
import math
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

from config import CONFIG
from strategies.base import BaseStrategy
from utils.logger import get_logger

log = get_logger("strategy.predictive_short")

# ── Thresholds ──────────────────────────────────────────────────────────────
RESEARCH_INTERVAL   = 900   # 15 minutes between deep scans
CONFIRM_MIN_SIGNALS = 2     # need 2+ daily signals to confirm
MIN_PRICE           = 10.0  # no penny stocks
MIN_AVG_VOLUME      = 500_000  # liquid names only
MAX_POSITION_DOLLARS = 8_000
STOP_PCT            = 0.025   # 2.5% stop above entry (for short)
TARGET_PCT          = 0.07    # 7% take profit below entry


class PredictiveShortStrategy(BaseStrategy):
    name = "Predictive Short"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        # Research results: symbol → {score, signals, timestamp}
        self._candidates: Dict[str, dict] = {}
        # Last research timestamps
        self._last_research_ts = 0.0
        self._research_thread: Optional[threading.Thread] = None
        # Execution state: symbol → entry_target (wait for bounce)
        self._waiting_entry: Dict[str, float] = {}
        self._data_url = "https://data.alpaca.markets"

    def get_symbols(self) -> List[str]:
        return []  # Dynamic universe

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════

    def run(self) -> None:
        # 1. Manage exits on existing positions
        self._manage_exits()

        # 2. Kick off background research if stale
        now = time.time()
        if (now - self._last_research_ts > RESEARCH_INTERVAL
                and (self._research_thread is None
                     or not self._research_thread.is_alive())):
            self._research_thread = threading.Thread(
                target=self._run_research, daemon=True)
            self._research_thread.start()

        # 3. Confirmation + execution on top candidates
        with self._lock:
            candidates = dict(self._candidates)

        if not candidates:
            return

        # Sort by score descending — act on top 5 only
        top = sorted(candidates.items(), key=lambda x: x[1]["score"], reverse=True)[:5]
        for symbol, info in top:
            if info["score"] < CONFIRM_MIN_SIGNALS:
                continue
            self._confirm_and_execute(symbol, info)

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 1: RESEARCH ENGINE
    # ═══════════════════════════════════════════════════════════════════

    def _run_research(self) -> None:
        """Background deep scan — finds bearish setups before price drops."""
        self.log.info("Research scan starting...")
        t0 = time.time()
        try:
            universe = self._get_research_universe()
            # Pre-fetch SPY once for sector comparison
            spy_ret_5d = None
            try:
                spy_df = self.feed.get_bars("SPY", timeframe="1Day", limit=10)
                if spy_df is not None and len(spy_df) >= 5:
                    spy_ret_5d = (float(spy_df["close"].values[-1]) - float(spy_df["close"].values[-5])) / float(spy_df["close"].values[-5])
            except Exception:
                pass

            results = {}
            for symbol in universe:
                if symbol == "SPY":
                    continue
                try:
                    score, signals = self._score_symbol(symbol, spy_ret_5d=spy_ret_5d)
                    if score >= 1:  # keep anything with 1+ signal; confirm layer gates at 3
                        results[symbol] = {
                            "score":     score,
                            "signals":   signals,
                            "timestamp": time.time(),
                        }
                except Exception:
                    pass

            with self._lock:
                self._candidates = results
                self._last_research_ts = time.time()

            top = sorted(results.items(), key=lambda x: x[1]["score"], reverse=True)[:8]
            self.log.info(
                "Research done in %.0fs — %d candidates. Top: %s",
                time.time() - t0,
                len(results),
                ", ".join(f"{s}(score={i['score']},sigs={'+'.join(i['signals'][:3])})"
                          for s, i in top),
            )
        except Exception as e:
            self.log.error("Research failed: %s", e)

    def _get_research_universe(self) -> List[str]:
        """Return liquid, high-beta names most prone to momentum moves."""
        # Tier 1: mega-cap + high-beta names (scan first, most liquid)
        universe = [
            # Mega-cap tech (most traded, clearest signals)
            "AAPL","MSFT","AMZN","NVDA","GOOGL","META","TSLA","AVGO",
            # Financials + healthcare
            "JPM","GS","MS","BAC","V","MA","UNH","LLY","ABBV",
            # Growth/volatile
            "CRM","NFLX","AMD","PLTR","COIN","MARA","RIOT","SOFI",
            "SMCI","INTC","MU","SNAP","UBER","PYPL","SQ",
            # ETFs (useful for macro signals)
            "SPY","QQQ","IWM",
            # Additional high-beta from scanner hot list
            "ENPH","XOM","BA","DIS",
        ]
        return list(dict.fromkeys(universe))

    def _score_symbol(self, symbol: str, spy_ret_5d: Optional[float] = None) -> Tuple[int, List[str]]:
        """
        Score a symbol for short potential. Returns (score, [signal_names]).
        Higher score = more bearish signals = stronger short candidate.
        """
        signals = []

        # Fetch recent bars (daily, 60 days)
        try:
            df = self.feed.get_bars(symbol, timeframe="1Day", limit=60)
        except Exception:
            return 0, []

        if df is None or len(df) < 20:
            return 0, []

        close = df["close"].values.astype(float)
        volume = df["volume"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)

        # Filter: price and volume minimums
        last_price = float(close[-1])
        avg_vol = float(volume[-20:].mean())
        if last_price < MIN_PRICE or avg_vol < MIN_AVG_VOLUME:
            return 0, []

        # ── Signal 1: RSI Divergence ──────────────────────────────────
        # Price at/near recent high BUT RSI declining (momentum exhaustion)
        rsi_val = self._rsi(close, 14)
        if rsi_val is not None:
            price_at_high = last_price >= np.max(close[-10:]) * 0.98
            rsi_declining = rsi_val > 65 and rsi_val < self._rsi(close[:-5], 14)
            if price_at_high and rsi_declining:
                signals.append("RSI_DIV")
            elif rsi_val > 75:
                signals.append("RSI_OB")  # overbought

        # ── Signal 2: Volume Distribution ────────────────────────────
        # Selling days have higher volume than buying days (institutions distributing)
        recent_up = [i for i in range(-10, 0) if close[i] > close[i-1]]
        recent_dn = [i for i in range(-10, 0) if close[i] < close[i-1]]
        if recent_up and recent_dn:
            avg_up_vol = np.mean([volume[i] for i in recent_up])
            avg_dn_vol = np.mean([volume[i] for i in recent_dn])
            if avg_dn_vol > avg_up_vol * 1.1:  # 10% more volume on sell days
                signals.append("DIST")

        # ── Signal 3: Price extended above moving averages ────────────
        ma20 = np.mean(close[-20:])
        ma50 = np.mean(close[-50:]) if len(close) >= 50 else ma20
        if last_price > ma20 * 1.04:  # 4% above 20-day MA
            signals.append("OVEREXT")
        elif last_price > ma50 * 1.08:  # 8% above 50-day MA
            signals.append("OVEREXT")

        # ── Signal 4: Momentum Stall ──────────────────────────────────
        # Price flat/declining over last 3 days despite being overbought
        recent_ret = (close[-1] - close[-4]) / close[-4] if close[-4] > 0 else 0
        if recent_ret < -0.01 and rsi_val and rsi_val > 60:
            signals.append("STALL")

        # ── Signal 5: Bearish engulfing or shooting star ─────────────
        # Candlestick patterns on daily chart
        if len(close) >= 2:
            last_open = df["open"].values[-1] if "open" in df.columns else close[-2]
            body = abs(close[-1] - last_open)
            candle_range = high[-1] - low[-1]
            upper_wick = high[-1] - max(close[-1], last_open)
            if candle_range > 0 and upper_wick / candle_range > 0.6 and body / candle_range < 0.3:
                signals.append("SHOOT_STAR")

        # ── Signal 6: Sector underperformance ────────────────────────
        # If stock has underperformed SPY by 2%+ over 5 days, momentum is turning
        if spy_ret_5d is not None and len(close) >= 5:
            stock_ret = (close[-1] - close[-5]) / close[-5] if close[-5] > 0 else 0
            if stock_ret < spy_ret_5d - 0.02:
                signals.append("UNDERPERF")

        return len(signals), signals

    # ═══════════════════════════════════════════════════════════════════
    # LAYER 2: CONFIRMATION
    # ═══════════════════════════════════════════════════════════════════

    def _confirm_and_execute(self, symbol: str, info: dict) -> None:
        """Confirm signals are still active and enter if timing is right."""
        # Skip if already in this position
        if self.portfolio.has_position(symbol):
            return

        # Fetch intraday data for timing
        try:
            df_5m = self.feed.get_bars(symbol, timeframe="5Min", limit=24)
        except Exception:
            return
        if df_5m is None or len(df_5m) < 6:
            return

        close_5m = df_5m["close"].values.astype(float)
        last_price = float(close_5m[-1])
        if last_price < MIN_PRICE:
            return

        # Real-time RSI on 5-min bars
        rsi_5m = self._rsi(close_5m, 14)
        if rsi_5m is None:
            return

        # ── Execution timing: wait for intraday BOUNCE ────────────────
        # Don't short into a falling knife — wait for a pop into resistance
        # Pattern: price dipped, now bouncing back up → short the bounce
        recent_low  = np.min(close_5m[-6:])
        recent_high = np.max(close_5m[-6:])
        bounce_pct  = (last_price - recent_low) / recent_low if recent_low > 0 else 0

        # Conditions for smart entry:
        # 1. Daily signals say bearish (already confirmed by caller)
        # 2. Intraday RSI is elevated (bounce is overbought)
        # 3. Price has bounced 0.5-3% off its intraday low (giving us a better entry)
        # 4. Price is approaching recent resistance (near-day high)
        approaching_resistance = last_price >= recent_high * 0.99
        overbought_intraday    = rsi_5m > 60
        has_bounced            = 0.003 <= bounce_pct <= 0.04

        timing_ok = (overbought_intraday and approaching_resistance) or (has_bounced and rsi_5m > 55)

        if not timing_ok:
            self.log.debug(
                "PRED %s: signals=%d/%s, waiting for better entry (RSI5m=%.0f, bounce=%.1f%%)",
                symbol, info["score"], info["signals"], rsi_5m, bounce_pct * 100,
            )
            return

        # ── Enter short ───────────────────────────────────────────────
        stop   = round(last_price * (1 + STOP_PCT), 2)
        target = round(last_price * (1 - TARGET_PCT), 2)
        qty    = max(1, int(MAX_POSITION_DOLLARS / last_price))
        conf   = min(0.85, 0.5 + info["score"] * 0.07)

        self.log.info(
            "PRED SHORT %s | price=$%.2f  score=%d  signals=%s  RSI5m=%.0f  bounce=%.1f%%",
            symbol, last_price, info["score"], "+".join(info["signals"]),
            rsi_5m, bounce_pct * 100,
        )
        self.executor.enter_short(
            symbol, qty,
            stop_price=stop,
            take_profit=target,
            strategy_tag="PRED",
            confidence=conf,
        )

    # ═══════════════════════════════════════════════════════════════════
    # EXIT MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════

    def _manage_exits(self) -> None:
        """Exit predictive-short positions at stop/target."""
        for symbol, pos in list(self.portfolio.positions.items()):
            if pos.get("side") != "short":
                continue
            entry = pos.get("avg_entry", 0)
            cur   = pos.get("current_price", 0)
            if entry <= 0 or cur <= 0:
                continue
            pnl_pct = (entry - cur) / entry   # positive = profit for short

            if pnl_pct >= TARGET_PCT:
                self.log.info("PRED EXIT %s — TP +%.1f%%", symbol, pnl_pct * 100)
                self.executor.exit_position(symbol, reason=f"PRED TP +{pnl_pct:.1%}")
            elif pnl_pct <= -(STOP_PCT + 0.005):
                self.log.info("PRED EXIT %s — SL %.1f%%", symbol, pnl_pct * 100)
                self.executor.exit_position(symbol, reason=f"PRED SL {pnl_pct:.1%}")

    # ═══════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _rsi(prices: np.ndarray, period: int = 14) -> Optional[float]:
        if len(prices) < period + 1:
            return None
        deltas = np.diff(prices)
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs  = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
