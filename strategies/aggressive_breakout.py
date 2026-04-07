"""
Ferchaud — Breakout Momentum Strategy
══════════════════════════════════════
Two modes controlled per-user or via env:

PASSIVE  — Lower risk, higher accuracy bar, fewer trades.
           Ideal for users who want steady compounding.
           MIN_SIGNALS=6, RISK=1%, no options, tighter stops.

AGGRESSIVE — High conviction, high turnover, options on big setups.
             MIN_SIGNALS=4, RISK=2%, options enabled, wider stops.

Signal stack (8 sources, max 14 points):
  1. Breakout above/below consolidation high/low  (+2)
  2. EMA alignment: 8 > 21 > 50 (trend confirmation) (+2)
  3. MACD histogram positive & rising             (+1)
  4. Volume spike > 1.5x avg                       (+1)
  5. ATR expansion > 1.2x recent                  (+1)
  6. Bollinger Band breakout                       (+1)
  7. RSI momentum (40-70 long, 30-60 short zone)  (+1)
  8. Relative volume rank (top 20% of day)        (+1)
  9. X Research / Grok sentiment boost            (+2)
  10. Full market scanner direction score          (+3 max)
"""
from __future__ import annotations
import os
from typing import List, Dict, Any
import numpy as np
import pandas as pd

from config import CONFIG
from data.indicators import ema, macd, atr, bollinger_bands
from data.x_research import x_researcher
from data.full_market_scanner import scanner
from strategies.base import BaseStrategy

# Mode can be overridden per-user via trading_mode field
GLOBAL_MODE = os.getenv("TRADING_MODE_STRATEGY", "aggressive")  # passive | aggressive


class ModeConfig:
    """Per-mode parameters."""
    CONFIGS: Dict[str, Dict[str, Any]] = {
        "passive": {
            "min_signals":       6,      # Higher bar — only very clear setups
            "risk_per_trade":    0.01,   # 1% equity risk per trade
            "stop_mult":         1.0,    # Tighter stop: 1x ATR
            "trail_mult":        2.0,    # Wider trail to let winners run
            "max_pos_pct":       0.10,   # Max 10% of cap per position
            "options_threshold": 999,    # Options disabled in passive
            "time_stop_candles": 8,      # Exit sooner if going nowhere
            "volume_spike_mult": 2.0,    # Need bigger volume confirmation
            "initial_size_frac": 0.50,   # Enter smaller
            "addon_frac":        0.50,   # Add on confirmation
            "rsi_long_min":      45,     # RSI must show momentum
            "rsi_long_max":      65,
            "rsi_short_min":     35,
            "rsi_short_max":     55,
        },
        "aggressive": {
            "min_signals":       4,      # Lower bar — more trades
            "risk_per_trade":    0.02,   # 2% equity risk per trade
            "stop_mult":         1.5,    # Wider stop: 1.5x ATR
            "trail_mult":        1.5,    # Tighter trail for faster locking
            "max_pos_pct":       0.15,   # Max 15% of cap per position
            "options_threshold": 6,      # Options at high conviction
            "time_stop_candles": 12,     # Give more time
            "volume_spike_mult": 1.5,    # Standard volume requirement
            "initial_size_frac": 0.60,
            "addon_frac":        0.40,
            "rsi_long_min":      40,
            "rsi_long_max":      70,
            "rsi_short_min":     30,
            "rsi_short_max":     60,
        },
    }

    @classmethod
    def get(cls, mode: str) -> Dict[str, Any]:
        return cls.CONFIGS.get(mode, cls.CONFIGS["aggressive"])


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def relative_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume as multiple of rolling average."""
    avg = volume.rolling(period).mean()
    return volume / avg.replace(0, np.nan)


class AggressiveBreakoutStrategy(BaseStrategy):
    name = "aggressive_breakout"

    EMA_FAST  = 8
    EMA_MID   = 21
    EMA_SLOW  = 50
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIG  = 9
    ATR_PERIOD = 14
    BB_PERIOD  = 20
    BB_STD     = 2.0
    CONSOL_PERIOD = 20
    RSI_PERIOD    = 14
    BARS_REQUIRED = 60

    def __init__(self, *args, mode: str = GLOBAL_MODE, **kwargs):
        super().__init__(*args, **kwargs)
        self._mode = mode
        self._cfg  = ModeConfig.get(mode)
        self.log.info("AggressiveBreakout init — mode=%s min_signals=%d risk=%.0f%%",
                      mode, self._cfg["min_signals"], self._cfg["risk_per_trade"] * 100)

    # ── Public mode setter (called by web bot manager) ─────────────────────
    def set_mode(self, mode: str) -> None:
        if mode not in ModeConfig.CONFIGS:
            return
        self._mode = mode
        self._cfg  = ModeConfig.get(mode)
        self.log.info("Mode changed → %s", mode)

    @property
    def mode(self) -> str:
        return self._mode

    def get_symbols(self) -> List[str]:
        hot = scanner.hot_symbols
        if hot:
            return hot[:40]
        trending = x_researcher.trending_tickers
        if trending:
            return trending[:20]
        return list(CONFIG.stock_universe)[:10]

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            self.log.warning("Risk halted — skipping breakout run")
            return
        if scanner.needs_refresh():
            scanner.scan_async()
        if x_researcher.needs_refresh():
            x_researcher.scan_async()
        symbols = self.get_symbols()
        self.log.info("[%s mode] Scanning %d symbols: %s…",
                      self._mode.upper(), len(symbols), ", ".join(symbols[:6]))
        for sym in symbols:
            try:
                self._process(sym)
            except Exception as e:
                self.log.error("Error on %s: %s", sym, e)

    # ── Core signal engine ────────────────────────────────────────────────────
    def _process(self, symbol: str) -> None:
        if self.should_skip_entry(symbol):
            return
        pos = self.portfolio.positions.get(symbol)
        if pos:
            self._manage_position(symbol, pos)
            return

        df = self.feed.get_bars(symbol, timeframe="15Min", limit=self.BARS_REQUIRED)
        if df is None or df.empty or len(df) < self.BARS_REQUIRED:
            return

        c = df["close"]
        h = df["high"]
        lo = df["low"]
        v = df["volume"]
        px   = float(c.iloc[-1])
        vx   = float(v.iloc[-1])
        cfg  = self._cfg

        # ── Indicators ────────────────────────────────────────────────────────
        ef   = float(ema(c, self.EMA_FAST).iloc[-1])
        em_  = float(ema(c, self.EMA_MID).iloc[-1])
        es   = float(ema(c, self.EMA_SLOW).iloc[-1])
        mh   = float(macd(c, self.MACD_FAST, self.MACD_SLOW, self.MACD_SIG)[2].iloc[-1])
        mh_p = float(macd(c, self.MACD_FAST, self.MACD_SLOW, self.MACD_SIG)[2].iloc[-2])
        atr_ = float(atr(h, lo, c, self.ATR_PERIOD).iloc[-1])
        atr_p = float(atr(h, lo, c, self.ATR_PERIOD).iloc[-5]) if len(c) > 5 else atr_
        ub, _, lb = bollinger_bands(c, self.BB_PERIOD, self.BB_STD)
        rsi_ = float(rsi(c, self.RSI_PERIOD).iloc[-1])
        rvol = float(relative_volume(v, self.CONSOL_PERIOD).iloc[-1])
        avg_vol = float(v.rolling(self.CONSOL_PERIOD).mean().iloc[-1])
        con_hi  = float(h.rolling(self.CONSOL_PERIOD).max().iloc[-2])
        con_lo  = float(lo.rolling(self.CONSOL_PERIOD).min().iloc[-2])

        # ── Score signals ─────────────────────────────────────────────────────
        sl, ss = 0, 0  # long score, short score

        # 1. Consolidation breakout
        if px > con_hi:   sl += 2
        if px < con_lo:   ss += 2

        # 2. EMA alignment
        if ef > em_ > es:  sl += 2
        elif ef < em_ < es: ss += 2

        # 3. MACD rising
        if mh > 0 and mh > mh_p:   sl += 1
        elif mh < 0 and mh < mh_p: ss += 1

        # 4. Volume spike
        if rvol >= cfg["volume_spike_mult"]:
            sl += 1; ss += 1

        # 5. ATR expansion
        if atr_ > atr_p * 1.2:
            sl += 1; ss += 1

        # 6. Bollinger breakout
        if px > float(ub.iloc[-1]): sl += 1
        if px < float(lb.iloc[-1]): ss += 1

        # 7. RSI filter (avoid overbought longs / oversold shorts)
        if cfg["rsi_long_min"] <= rsi_ <= cfg["rsi_long_max"]:   sl += 1
        if cfg["rsi_short_min"] <= rsi_ <= cfg["rsi_short_max"]: ss += 1

        # 8. Relative volume rank
        if rvol >= 1.0 and rvol > 1.5: sl += 1; ss += 1

        # 9. X Research sentiment
        td = x_researcher.trending_details
        if symbol in td:
            sent = td[symbol].get("sentiment", "")
            if sent == "bullish":  sl += 2
            elif sent == "bearish": ss += 2

        # 10. Scanner score
        sd = scanner.hot_details.get(symbol, {})
        if sd:
            score = sd.get("score", 0)
            if sd.get("direction") == "long":   sl += min(3, score // 3)
            elif sd.get("direction") == "short": ss += min(3, score // 3)

        # ── Decision ──────────────────────────────────────────────────────────
        min_sig = cfg["min_signals"]
        if sl >= min_sig and sl > ss:
            if sl >= cfg["options_threshold"]:
                self._enter_options(symbol, "long", px, atr_, sl)
            self._enter_breakout(symbol, "long", px, atr_, sl)
        elif ss >= min_sig and ss > sl:
            if ss >= cfg["options_threshold"]:
                self._enter_options(symbol, "short", px, atr_, ss)
            self._enter_breakout(symbol, "short", px, atr_, ss)

    def _enter_breakout(self, symbol: str, direction: str, price: float,
                        atr_val: float, signals: int) -> None:
        cfg = self._cfg
        equity = self.portfolio.equity
        cap = min(equity, getattr(CONFIG, "virtual_cap", 5000.0))

        risk_usd     = cap * cfg["risk_per_trade"]
        stop_dist    = cfg["stop_mult"] * atr_val
        if stop_dist <= 0:
            return
        target_usd   = (risk_usd / stop_dist) * price
        initial_usd  = target_usd * cfg["initial_size_frac"]
        initial_usd  = min(initial_usd, cap * cfg["max_pos_pct"])
        if initial_usd < 10:
            return

        qty = initial_usd / price
        is_crypto = "/" in symbol
        qty = round(qty, 4) if is_crypto else max(1, int(qty))

        stop  = price - stop_dist if direction == "long" else price + stop_dist
        tp    = price + cfg["trail_mult"] * atr_val if direction == "long" else price - cfg["trail_mult"] * atr_val
        conf  = min(1.0, signals / 10.0)

        self.log.info(
            "ENTRY %s %s $%.2f | sig=%d conf=%.0f%% | stop=$%.2f tp=$%.2f | $%.0f | mode=%s",
            direction.upper(), symbol, price, signals, conf * 100,
            stop, tp, initial_usd, self._mode
        )
        if direction == "long":
            self.executor.enter_long(symbol, qty, stop_price=stop, take_profit=tp,
                                     strategy_tag=f"breakout_{self._mode}", confidence=conf)
        else:
            self.executor.enter_short(symbol, qty, stop_price=stop, take_profit=tp,
                                      strategy_tag=f"breakout_{self._mode}", confidence=conf)

    def _manage_position(self, symbol: str, pos: dict) -> None:
        df = self.feed.get_bars(symbol, timeframe="15Min", limit=20)
        if df is None or df.empty:
            return
        c, h, lo = df["close"], df["high"], df["low"]
        px    = float(c.iloc[-1])
        atr_v = float(atr(h, lo, c, self.ATR_PERIOD).iloc[-1])
        entry = pos.get("avg_entry", px)
        plpc  = pos.get("unrealized_plpc", 0)
        side  = pos.get("side", "long")
        cfg   = self._cfg

        # Hard stop
        if side == "long" and px < entry - cfg["stop_mult"] * atr_v:
            self.executor.exit_position(symbol, reason="hard_stop"); return
        if side == "short" and px > entry + cfg["stop_mult"] * atr_v:
            self.executor.exit_position(symbol, reason="hard_stop"); return

        # Time stop — flat after N candles
        if abs(plpc) < 0.003:
            self.executor.exit_position(symbol, reason="time_stop"); return

        # Partial profit at trail target
        if side == "long" and px > entry + cfg["trail_mult"] * atr_v:
            self.executor.exit_position(symbol, reason="partial_profit")
        elif side == "short" and px < entry - cfg["trail_mult"] * atr_v:
            self.executor.exit_position(symbol, reason="partial_profit")

    def _enter_options(self, symbol: str, direction: str, price: float,
                       atr_val: float, signals: int) -> None:
        try:
            option = scanner.get_options_chain(symbol, direction, days_out=7)
            if not option:
                return
            cap  = min(self.portfolio.equity, getattr(CONFIG, "virtual_cap", 5000.0))
            max_usd = min(200, cap * 0.04)
            try:
                q = self.feed._api.get_latest_quote(option["symbol"])
                opt_px = float(q.ap) if q.ap else float(q.bp) * 1.05
            except Exception:
                opt_px = atr_val * 0.5
            if opt_px <= 0 or opt_px * 100 > max_usd:
                return
            qty = max(1, int(max_usd / (opt_px * 100)))
            self.log.info("OPTIONS %s %s strike=$%.2f qty=%d @ $%.2f",
                          option["type"].upper(), symbol, option["strike"], qty, opt_px)
            self.executor._broker._api.submit_order(
                symbol=option["symbol"], qty=qty, side="buy",
                type="market", time_in_force="day"
            )
        except Exception as e:
            self.log.debug("Options failed %s: %s", symbol, e)


# Singleton exposed to bot manager
strategy_instance: AggressiveBreakoutStrategy | None = None
