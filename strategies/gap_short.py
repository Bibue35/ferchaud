"""
Gap / Big-Mover Short Strategy
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scans the ENTIRE market every cycle using Alpaca's screener API.
Finds stocks down 5%+ from previous close (gap-down or intraday collapse).
Enters short positions expecting continuation of the selling pressure.

Also catches gap-UP stocks (up 7%+) for potential fade shorts on exhaustion.
"""
import time
from typing import List, Tuple

import requests

from config import CONFIG
from strategies.base import BaseStrategy
from utils.logger import get_logger

log = get_logger("strategy.gap_short")

# Minimum % drop to trigger a short
MIN_DROP_PCT   = -5.0   # down 5%+ → short candidate
MAX_DROP_PCT   = -40.0  # ignore circuit-breaker / halted stocks

# Minimum $ price and volume filters (avoid penny stocks)
MIN_PRICE      = 5.0
MIN_VOLUME     = 200_000   # daily volume

# Stop/target levels relative to entry
STOP_MULT      = 1.025   # stop 2.5% above entry (for short)
TARGET_MULT    = 0.93    # target 7% below entry

# Cache refresh: only re-scan every N seconds
SCAN_INTERVAL  = 120     # 2 minutes between full market scans


class GapShortStrategy(BaseStrategy):
    name = "Gap/Mover Short"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_scan_ts: float = 0.0
        self._cached_movers: List[dict] = []
        self._data_url = "https://data.alpaca.markets"

    def get_symbols(self) -> List[str]:
        return []  # Dynamic — pulled from screener

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run(self) -> None:
        # Exit management first — check existing gap-short positions
        self._manage_exits()

        now = time.time()
        if now - self._last_scan_ts > SCAN_INTERVAL:
            self._cached_movers = self._fetch_movers()
            self._last_scan_ts = now

        if not self._cached_movers:
            return

        regime_scale = self._get_regime_scale()
        for stock in self._cached_movers:
            symbol    = stock["symbol"]
            pct_chg   = stock["pct_change"]
            price     = stock["price"]
            volume    = stock["volume"]

            # Filters
            if price < MIN_PRICE or volume < MIN_VOLUME:
                continue

            # Already have a position in this symbol?
            if self.portfolio.has_position(symbol):
                continue

            # ── Gap-down short (selling pressure, momentum continuation) ──
            if pct_chg <= MIN_DROP_PCT:
                stop   = round(price * STOP_MULT, 2)
                target = round(price * TARGET_MULT, 2)
                qty    = self._size(price)
                if qty < 1:
                    continue
                conf = min(0.9, 0.6 + abs(pct_chg) * 0.03)
                self.log.info(
                    "GAP SHORT %s | chg=%.1f%%  price=$%.2f  vol=%d  qty=%d",
                    symbol, pct_chg, price, volume, qty,
                )
                self.executor.enter_short(
                    symbol, qty,
                    stop_price=stop,
                    take_profit=target,
                    strategy_tag="GAP",
                    confidence=conf,
                )

    # ── Market scanner ────────────────────────────────────────────────────────

    def _fetch_movers(self) -> List[dict]:
        """Call Alpaca screener API for biggest losers today."""
        headers = {
            "APCA-API-KEY-ID":     CONFIG.api_key,
            "APCA-API-SECRET-KEY": CONFIG.secret_key,
        }
        results = []

        # Losers (stocks down most %)
        try:
            r = requests.get(
                f"{self._data_url}/v1beta1/screener/stocks/movers",
                params={"top": 50},
                headers=headers,
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json()
                losers  = data.get("losers",  [])
                gainers = data.get("gainers", [])
                for item in losers + gainers:
                    sym     = item.get("symbol", "")
                    chg_pct = float(item.get("percent_change", 0))
                    price   = float(item.get("price", 0))
                    volume  = int(item.get("volume", 0))
                    # Filter out warrants, rights, units (non-shortable junk)
                    if not sym or price <= 0:
                        continue
                    if any(c in sym for c in ['.', '+']) or sym.endswith('W') or sym.endswith('R'):
                        continue
                    if len(sym) > 5:   # typical warrants have long tickers
                        continue
                    results.append({
                        "symbol":     sym,
                        "pct_change": chg_pct,
                        "price":      price,
                        "volume":     volume,
                    })
                top_losers = sorted(results, key=lambda x: x["pct_change"])[:8]
                self.log.info(
                    "Mover scan: %d losers, %d gainers. Top losers: %s",
                    len(losers), len(gainers),
                    ", ".join(f"{s['symbol']}({s['pct_change']:+.1f}%)" for s in top_losers),
                )
            else:
                self.log.warning("Screener API %d: %s", r.status_code, r.text[:200])
        except Exception as e:
            self.log.error("_fetch_movers: %s", e)

        return results

    # ── Exit management ───────────────────────────────────────────────────────

    def _manage_exits(self) -> None:
        """Exit gap-short positions that hit stop or target."""
        for symbol, pos in list(self.portfolio.positions.items()):
            if pos.get("side") != "short":
                continue
            entry = pos.get("avg_entry", 0)
            cur   = pos.get("current_price", 0)
            if entry <= 0 or cur <= 0:
                continue
            pnl_pct = (entry - cur) / entry   # positive = profit for short

            # Take profit: +7% gain
            if pnl_pct >= 0.07:
                self.log.info("GAP EXIT %s — take profit %.1f%%", symbol, pnl_pct * 100)
                self.executor.exit_position(symbol, reason=f"GAP: TP +{pnl_pct:.1%}")
            # Stop loss: -3% loss (price moved against us)
            elif pnl_pct <= -0.03:
                self.log.info("GAP EXIT %s — stop loss %.1f%%", symbol, pnl_pct * 100)
                self.executor.exit_position(symbol, reason=f"GAP: SL {pnl_pct:.1%}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _size(self, price: float) -> int:
        """Risk-based position size, capped at $8K per trade."""
        max_dollars = 8_000
        qty = int(max_dollars / price)
        return max(qty, 1) if price <= max_dollars else 0

    def _get_regime_scale(self) -> float:
        try:
            return self.regime.scale if self.regime else 1.0
        except Exception:
            return 1.0
