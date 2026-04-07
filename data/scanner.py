"""
Universe Scanner — Scans 150+ stocks for opportunities
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finds: top movers, volume spikes, gaps, high-volatility names.
Refreshes every 5 minutes. Feeds hot symbols into strategies.
"""
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.logger import get_logger

log = get_logger("data.scanner")


SCAN_UNIVERSE = [
    # Mega-cap tech (highest volume, fastest moves)
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AVGO","AMD","MU",
    # High-vol semi & AI
    "ARM","SMCI","INTC","QCOM","PLTR","SOUN","IONQ","RGTI",
    # Software movers
    "CRM","SNOW","NET","CRWD","DDOG",
    # Fintech (high vol)
    "COIN","SOFI","SQ","PYPL","HOOD","AFRM",
    # Meme / crypto-adjacent (biggest daily swings)
    "GME","AMC","MARA","RIOT","CLSK","BITF","WULF",
    # Biotech catalysts
    "MRNA","PFE","LLY","REGN",
    # Energy movers
    "XOM","OXY","FSLR","ENPH",
    # EV (high vol)
    "RIVN","LCID","NIO",
    # ETFs (regime signals)
    "SPY","QQQ","IWM","SOXL","TQQQ","ARKK",
    # Entertainment / consumer
    "DIS","NFLX","ROKU","RBLX","DKNG",
    # High-beta misc
    "BA","UBER","ABNB","RKLB",
]


class UniverseScanner:
    """Scans 150+ stocks, scores by opportunity, returns top movers.
    Non-blocking: scan runs in a background thread so it never stalls strategies."""

    def __init__(self, api) -> None:
        self._api = api
        self._last_scan: Optional[datetime] = None
        self._scan_interval = timedelta(minutes=5)
        self._hot_stocks: Dict[str, dict] = {}
        self._scanning = False
        self._scan_thread: Optional[threading.Thread] = None
    def _throttle(self) -> None:
        """Light throttle — just space out requests. Alpaca SDK handles 429 retries."""
        time.sleep(0.4)

    @property
    def hot_symbols(self) -> List[str]:
        sorted_stocks = sorted(
            self._hot_stocks.items(),
            key=lambda x: x[1].get("score", 0),
            reverse=True,
        )
        return [sym for sym, _ in sorted_stocks[:50]]

    def needs_refresh(self) -> bool:
        if self._last_scan is None:
            return True
        return datetime.now(timezone.utc) - self._last_scan > self._scan_interval

    def scan(self) -> List[str]:
        """Non-blocking scan. Kicks off background thread if not already running.
        Always returns current cached hot_symbols immediately."""
        if self._scanning:
            return self.hot_symbols
        self._scanning = True
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()
        return self.hot_symbols

    def _scan_worker(self) -> None:
        """Background worker that scores all symbols."""
        try:
            log.info("Scanning %d symbols (background)...", len(SCAN_UNIVERSE))
            results: Dict[str, dict] = {}
            batch_size = 20
            total = len(SCAN_UNIVERSE)
            for i in range(0, total, batch_size):
                log.info("Scanner batch %d/%d...", i // batch_size + 1, (total + batch_size - 1) // batch_size)
                batch = SCAN_UNIVERSE[i:i + batch_size]
                for symbol in batch:
                    try:
                        data = self._score_symbol(symbol)
                        if data and data["score"] > 0:
                            results[symbol] = data
                    except Exception:
                        pass
                time.sleep(1.0)
            self._hot_stocks = results
            self._last_scan = datetime.now(timezone.utc)
            hot = self.hot_symbols
            if hot:
                top5 = hot[:5]
                log.info("Scan done — %d hot. Top: %s", len(self._hot_stocks),
                         ", ".join(f"{s}({self._hot_stocks[s]['score']:.1f})" for s in top5))
        except Exception as e:
            import traceback
            log.error("Scanner error: %s\n%s", e, traceback.format_exc())
        finally:
            self._scanning = False
            log.info("Scanner thread finished (scanning=%s)", self._scanning)


    def _score_symbol(self, symbol: str) -> Optional[dict]:
        self._throttle()
        start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        end = datetime.now(timezone.utc).isoformat()
        bars = self._api.get_bars(symbol, "1Day", limit=20, feed="iex", start=start, end=end)
        bar_list = list(bars) if bars else []
        if len(bar_list) < 5:
            return None

        closes = [float(b.c) for b in bar_list]
        volumes = [float(b.v) for b in bar_list]
        highs = [float(b.h) for b in bar_list]
        lows = [float(b.l) for b in bar_list]
        opens = [float(b.o) for b in bar_list]

        current = closes[-1]
        prev_close = closes[-2]
        if current <= 0 or prev_close <= 0:
            return None

        daily_change = (current - prev_close) / prev_close
        avg_vol = np.mean(volumes[:-1]) if len(volumes) > 1 else volumes[0]
        vol_ratio = volumes[-1] / max(avg_vol, 1)

        # ATR as % of price
        atr_vals = []
        for j in range(1, len(closes)):
            tr = max(highs[j] - lows[j], abs(highs[j] - closes[j-1]),
                     abs(lows[j] - closes[j-1]))
            atr_vals.append(tr)
        atr_pct = (np.mean(atr_vals[-10:]) / current * 100) if current > 0 else 0


        # 5-day momentum
        momentum_5d = (current - closes[-6]) / closes[-6] if len(closes) > 5 else daily_change
        # Gap
        gap_pct = abs(opens[-1] - closes[-2]) / closes[-2] if closes[-2] > 0 else 0

        score = (
            abs(daily_change) * 30
            + max(vol_ratio - 1, 0) * 5
            + atr_pct * 3
            + abs(momentum_5d) * 20
            + gap_pct * 40
        )
        if abs(daily_change) > 0.05:
            score *= 1.5
        if vol_ratio > 3.0:
            score *= 1.3

        return {
            "score": float(score),
            "daily_change": float(daily_change),
            "vol_ratio": float(vol_ratio),
            "atr_pct": float(atr_pct),
            "momentum_5d": float(momentum_5d),
            "gap_pct": float(gap_pct),
            "price": float(current),
            "direction": "bull" if daily_change > 0 else "bear",
        }

    def get_movers(self, min_pct: float = 0.03) -> Tuple[List[str], List[str]]:
        gainers = [s for s, d in self._hot_stocks.items() if d["daily_change"] > min_pct]
        losers = [s for s, d in self._hot_stocks.items() if d["daily_change"] < -min_pct]
        gainers.sort(key=lambda s: self._hot_stocks[s]["daily_change"], reverse=True)
        losers.sort(key=lambda s: self._hot_stocks[s]["daily_change"])
        return gainers, losers

    def get_volume_spikes(self, min_ratio: float = 2.5) -> List[str]:
        spikes = [s for s, d in self._hot_stocks.items() if d["vol_ratio"] > min_ratio]
        spikes.sort(key=lambda s: self._hot_stocks[s]["vol_ratio"], reverse=True)
        return spikes

    def get_volatile(self, min_atr_pct: float = 3.0) -> List[str]:
        vol = [s for s, d in self._hot_stocks.items() if d["atr_pct"] > min_atr_pct]
        vol.sort(key=lambda s: self._hot_stocks[s]["atr_pct"], reverse=True)
        return vol
