"""
Catalyst / News-Driven Strategy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trades on breaking news, volume spikes, momentum bursts, gaps.
Uses UniverseScanner to find hottest 150+ stocks.
Targets stocks moving 10-30%+ in days.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import numpy as np

from config import CONFIG
from data.indicators import atr as calc_atr
from data.sentiment import _score_headline, _time_decay_weight
from strategies.base import BaseStrategy
from utils.logger import get_logger

log = get_logger("strategy.catalyst")


class CatalystStrategy(BaseStrategy):
    name = "catalyst"

    MAX_POSITIONS = 15
    ATR_STOP_MULT = 2.5       # Wider stops for catalyst plays
    ATR_TARGET_MULT = 5.0     # Big targets — want 10-30% moves
    NEWS_THRESHOLD = 0.30
    VOL_SPIKE_RATIO = 2.5
    GAP_THRESHOLD = 0.02
    BURST_PCT = 0.04
    MIN_CONFIDENCE = 0.60


    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._news_cache: Dict[str, float] = {}
        self._news_ts: float = 0
        self._traded_today: Set[str] = set()
        self._scanner = None

    def get_symbols(self) -> List[str]:
        return CONFIG.stock_universe + CONFIG.crypto_universe

    def _get_scanner_symbols(self) -> List[str]:
        """Get hot symbols from scanner if available."""
        if self._scanner is None:
            try:
                from data.scanner import UniverseScanner
                self._scanner = UniverseScanner(self.executor._broker._api)
            except Exception:
                return []
        if self._scanner.needs_refresh():
            try:
                return self._scanner.scan()
            except Exception as e:
                self.log.error("Scanner failed: %s", e)
                return self._scanner.hot_symbols
        return self._scanner.hot_symbols

    def run(self) -> None:
        self.portfolio.refresh()
        if self.risk.is_halted:
            return


        # Reset daily set at midnight
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute < 2:
            self._traded_today.clear()

        current_pos = sum(1 for s in self.portfolio.positions
                          if self.portfolio.has_position(s))
        if current_pos >= self.MAX_POSITIONS:
            return

        # Get top hot symbols from scanner — limit to top 15 to avoid API overload
        hot = self._get_scanner_symbols()
        config_syms = self.get_symbols()
        # Hot first (already sorted by score), fill remainder from config up to 20 total
        all_symbols = list(dict.fromkeys(hot[:12] + config_syms))[:20]

        # Refresh news (cached 10min)
        self._refresh_news(all_symbols[:20])

        for symbol in all_symbols:
            if current_pos >= self.MAX_POSITIONS:
                break
            if symbol in self._traded_today:
                continue
            try:
                traded = self._process(symbol)
                if traded:
                    current_pos += 1
                    self._traded_today.add(symbol)
            except Exception as exc:
                self.log.error("Catalyst error %s: %s", symbol, exc)


    def _process(self, symbol: str) -> bool:
        """Check catalyst signals. Returns True if trade placed."""
        # Exit check for existing positions
        if self.portfolio.has_position(symbol):
            return self._check_exit(symbol)

        if self.should_skip_entry(symbol):
            return False

        df = self.feed.get_bars(symbol, timeframe="1D", limit=30)
        if df.empty or len(df) < 10:
            return False

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        price = close.iloc[-1]
        prev = close.iloc[-2] if len(close) > 1 else price
        daily_chg = (price - prev) / prev if prev > 0 else 0

        avg_vol = volume.iloc[-20:].mean() if len(volume) >= 20 else volume.mean()
        vol_ratio = volume.iloc[-1] / max(avg_vol, 1)

        atr_vals = calc_atr(high, low, close, 14)
        cur_atr = atr_vals.iloc[-1] if not atr_vals.empty else price * 0.02

        mom5 = (price - close.iloc[-6]) / close.iloc[-6] if len(close) > 5 else 0


        # ── Score catalyst signals ────────────────────────────────────
        signals = 0
        conf_boost = 0.0
        direction = None

        # 1. NEWS SENTIMENT
        news = self._news_cache.get(symbol, 0.0)
        if abs(news) > self.NEWS_THRESHOLD:
            signals += 1
            conf_boost += min(abs(news) * 0.3, 0.15)
            direction = "buy" if news > 0 else "sell"

        # 2. VOLUME SPIKE
        if vol_ratio > self.VOL_SPIKE_RATIO:
            signals += 1
            conf_boost += min((vol_ratio - 2) * 0.05, 0.15)
            if direction is None:
                direction = "buy" if daily_chg > 0 else "sell"

        # 3. MOMENTUM BURST (big daily move)
        if abs(daily_chg) > self.BURST_PCT:
            signals += 1
            conf_boost += min(abs(daily_chg) * 2, 0.20)
            if direction is None:
                direction = "buy" if daily_chg > 0 else "sell"


        # 4. GAP EVENT
        if "open" in df.columns and len(df) > 1:
            gap = abs(df["open"].iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            if gap > self.GAP_THRESHOLD:
                signals += 1
                conf_boost += min(gap * 3, 0.15)
                if direction is None:
                    direction = "buy" if df["open"].iloc[-1] > close.iloc[-2] else "sell"

        # 5. STRONG 5-DAY MOMENTUM (>10% in 5 days)
        if abs(mom5) > 0.10:
            signals += 1
            conf_boost += 0.10
            if direction is None:
                direction = "buy" if mom5 > 0 else "sell"

        # 6. Scanner data bonus
        if self._scanner and symbol in self._scanner._hot_stocks:
            sdata = self._scanner._hot_stocks[symbol]
            if sdata.get("score", 0) > 5.0:
                signals += 1
                conf_boost += 0.05

        # Need >= 2 signals
        if signals < 2 or direction is None:
            return False

        confidence = min(self.MIN_CONFIDENCE + conf_boost, 0.95)


        # ── Execute ───────────────────────────────────────────────────
        scale = self.regime_scale
        stop, target = self.risk.atr_stops(
            price, cur_atr, direction,
            self.ATR_STOP_MULT, self.ATR_TARGET_MULT,
        )
        stop_dist = abs(price - stop)
        qty = self.risk.risk_based_size(
            self.portfolio.equity, price, stop_dist,
            regime_scale=scale, confidence=confidence,
        )
        if qty < 1:
            return False

        self.log.info(
            "CATALYST %s %s | sig=%d conf=%.0f%% news=%.2f vol=%.1fx "
            "chg=%.1f%% mom5=%.1f%%",
            direction.upper(), symbol, signals, confidence * 100,
            news, vol_ratio, daily_chg * 100, mom5 * 100,
        )
        if direction == "buy":
            self.executor.enter_long(symbol, qty, stop_price=stop,
                                     take_profit=target, strategy_tag="CAT",
                                     confidence=confidence)
        else:
            self.executor.enter_short(symbol, qty, stop_price=stop,
                                      take_profit=target, strategy_tag="CAT",
                                      confidence=confidence)
        return True


    def _check_exit(self, symbol: str) -> bool:
        """Exit when momentum fades (2 consecutive bars against position)."""
        df = self.feed.get_bars(symbol, timeframe="1D", limit=5)
        if df.empty or len(df) < 3:
            return False
        close = df["close"]
        side = self.portfolio.position_side(symbol)
        if side == "long" and close.iloc[-1] < close.iloc[-2] < close.iloc[-3]:
            self.executor.exit_position(symbol, "CAT: momentum faded")
            return True
        if side == "short" and close.iloc[-1] > close.iloc[-2] > close.iloc[-3]:
            self.executor.exit_position(symbol, "CAT: momentum faded")
            return True
        return False

    def _refresh_news(self, symbols: List[str]) -> None:
        """Fetch news sentiment via Alpaca (10-min cache)."""
        now = time.time()
        if now - self._news_ts < 600:
            return
        try:
            api = self.executor._broker._api
            utc_now = datetime.now(timezone.utc)
            for symbol in symbols:
                try:
                    clean = symbol.replace("/USD", "").replace("/", "")
                    news = api.get_news(clean, limit=10)
                    if not news:
                        continue


                    weighted_sum = 0.0
                    weight_sum = 0.0
                    for article in news:
                        headline = getattr(article, "headline", "") or ""
                        summary = getattr(article, "summary", "") or ""
                        score = _score_headline(f"{headline} {summary}")
                        if score == 0.0:
                            continue
                        at = article.created_at
                        if isinstance(at, str):
                            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
                        if at.tzinfo is None:
                            at = at.replace(tzinfo=timezone.utc)
                        w = _time_decay_weight(at, utc_now, halflife_hours=6.0)
                        weighted_sum += score * w
                        weight_sum += w
                    if weight_sum > 0:
                        self._news_cache[symbol] = weighted_sum / weight_sum
                except Exception:
                    continue
            self._news_ts = now
            scored = {s: v for s, v in self._news_cache.items() if abs(v) > 0.1}
            if scored:
                top = sorted(scored.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
                log.info("News: %s", "  ".join(f"{s}={v:+.2f}" for s, v in top))
        except Exception as e:
            log.error("News refresh failed: %s", e)
