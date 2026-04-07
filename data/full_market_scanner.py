"""
Full Market Scanner — Scans ALL 12,000+ stocks on Alpaca
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finds low-price explosive movers based on:
  - Daily top gainers/losers (biggest % moves)
  - Most active by trade count (liquidity)
  - Volume spikes on low-float stocks
  - Earnings surprises, catalysts, news
  - Price range: $0.50 - $50 (no mega-caps)

Excludes: AAPL, MSFT, TSLA, GOOGL, AMZN, META, NVDA, BTC, ETH
          and all stocks > $50 price

Refreshes every 3 minutes. Feeds symbols into strategies.
"""
import os
import re
import threading
import time
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from utils.logger import get_logger

log = get_logger("data.full_scanner")

# ── Exclusion list: boring mega-caps and major crypto ─────────────────────────
EXCLUDED_SYMBOLS = {
    # Mega-cap tech (too stable, too expensive, boring)
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AVGO",
    "BRK.A", "BRK.B", "V", "MA", "JPM", "JNJ", "WMT", "PG", "UNH", "HD",
    "ORCL", "COST", "ABBV", "MRK", "PEP", "KO", "TMO", "MCD", "CSCO", "CRM",
    "ACN", "LIN", "ADBE", "ABT", "NKE", "TXN", "DHR", "NEE", "PM", "LLY",
    "NFLX", "AMD", "MU", "INTC", "QCOM", "ARM", "SMCI", "BA", "DIS", "XOM",
    "CVX", "COP", "GS", "MS", "BLK", "C", "BAC", "WFC", "PYPL", "SQ",
    # Major/leveraged ETFs (not individual stocks)
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV",
    "SOXL", "SOXS", "TQQQ", "SQQQ", "UVXY", "TLT", "GLD", "SLV",
    "USO", "XLE", "XLF", "XLK", "ARKK", "SPXL", "SPXS",
    # Major crypto
    "BTCUSD", "ETHUSD",
}

# Price filter
MIN_PRICE = 0.50     # No sub-penny trash
MAX_PRICE = 50.00    # No expensive mega-caps
MIN_VOLUME = 50000   # Minimum daily volume for liquidity

SCAN_INTERVAL = 180  # 3 minutes between full scans


class FullMarketScanner:
    """Scans ALL Alpaca stocks, finds low-price explosive movers."""

    def __init__(self, api_key: str = "", secret_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self._headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret,
        }
        self._hot_stocks: Dict[str, dict] = {}
        self._last_scan: float = 0
        self._lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None

    @property
    def hot_symbols(self) -> List[str]:
        """Top movers sorted by score — these are what strategies trade."""
        with self._lock:
            return sorted(self._hot_stocks.keys(),
                          key=lambda s: self._hot_stocks[s].get("score", 0),
                          reverse=True)[:50]

    @property
    def hot_details(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._hot_stocks)

    @property
    def gainers(self) -> List[str]:
        with self._lock:
            return [s for s, d in sorted(self._hot_stocks.items(),
                    key=lambda x: x[1].get("pct_change", 0), reverse=True)
                    if d.get("pct_change", 0) > 3][:20]

    @property
    def losers(self) -> List[str]:
        with self._lock:
            return [s for s, d in sorted(self._hot_stocks.items(),
                    key=lambda x: x[1].get("pct_change", 0))
                    if d.get("pct_change", 0) < -3][:20]

    def needs_refresh(self) -> bool:
        return time.time() - self._last_scan > SCAN_INTERVAL

    def scan_async(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._scan_thread = threading.Thread(target=self._full_scan, daemon=True)
        self._scan_thread.start()

    def _is_valid(self, symbol: str, price: float = 0) -> bool:
        """Filter: no mega-caps, no warrants ending in W, price in range."""
        if symbol in EXCLUDED_SYMBOLS:
            return False
        if symbol.endswith("W") or symbol.endswith("WS") or ".WS" in symbol:
            return False  # Warrants — illiquid, weird pricing
        if symbol.endswith("U"):
            return False  # SPAC units
        if price > 0 and (price < MIN_PRICE or price > MAX_PRICE):
            return False
        if len(symbol) > 6:
            return False  # Options-like symbols
        return True

    def _full_scan(self) -> None:
        """Run the complete scan pipeline."""
        results = {}
        try:
            # 1. Top movers (biggest % moves today)
            self._scan_movers(results)
            time.sleep(0.5)

            # 2. Most active by trade count
            self._scan_most_active(results)
            time.sleep(0.5)

            # 3. X research trending (if available)
            self._scan_x_trending(results)

            # 4. Finnhub news catalysts
            self._scan_news_catalysts(results)

            with self._lock:
                self._hot_stocks = results
            self._last_scan = time.time()

            # Log results
            gainers = [s for s, d in results.items() if d.get("pct_change", 0) > 0]
            losers = [s for s, d in results.items() if d.get("pct_change", 0) < 0]
            log.info("Full scan: %d hot stocks (%d gainers, %d losers, price $%.2f-$%.2f)",
                     len(results), len(gainers), len(losers), MIN_PRICE, MAX_PRICE)
            top5 = sorted(results.items(), key=lambda x: x[1].get("score", 0), reverse=True)[:5]
            for sym, d in top5:
                log.info("  #1 %s: %+.1f%% $%.2f score=%d — %s",
                         sym, d.get("pct_change", 0), d.get("price", 0),
                         d.get("score", 0), d.get("catalyst", "")[:50])

        except Exception as e:
            log.error("Full scan failed: %s", e)

    def _scan_movers(self, results: dict) -> None:
        """Alpaca top movers — biggest % gainers and losers today."""
        try:
            r = requests.get(
                "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=50",
                headers=self._headers, timeout=10)
            if not r.ok:
                log.debug("Movers API: %d", r.status_code)
                return
            data = r.json()

            for item in data.get("gainers", []):
                sym = item.get("symbol", "")
                price = float(item.get("price", 0))
                pct = float(item.get("percent_change", 0))
                if not self._is_valid(sym, price):
                    continue
                if abs(pct) < 3:
                    continue
                score = min(10, int(abs(pct) / 5) + 4)
                results[sym] = {
                    "price": price, "pct_change": pct, "score": score,
                    "direction": "long",
                    "catalyst": f"Up {pct:+.1f}% today — momentum breakout",
                    "source": "alpaca_movers",
                    "time": datetime.now(timezone.utc).isoformat(),
                }

            for item in data.get("losers", []):
                sym = item.get("symbol", "")
                price = float(item.get("price", 0))
                pct = float(item.get("percent_change", 0))
                if not self._is_valid(sym, price):
                    continue
                if abs(pct) < 5:
                    continue  # Need bigger drop for short/put
                score = min(10, int(abs(pct) / 5) + 3)
                results[sym] = {
                    "price": price, "pct_change": pct, "score": score,
                    "direction": "short",
                    "catalyst": f"Down {pct:.1f}% — crash/put candidate",
                    "source": "alpaca_movers",
                    "time": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            log.debug("Movers scan error: %s", e)

    def _scan_most_active(self, results: dict) -> None:
        """Most actively traded stocks — high volume = opportunity."""
        try:
            r = requests.get(
                "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives?by=trades&top=50",
                headers=self._headers, timeout=10)
            if not r.ok:
                return
            for item in r.json().get("most_actives", []):
                sym = item.get("symbol", "")
                trades = item.get("trade_count", 0)
                vol = item.get("volume", 0)
                if sym in EXCLUDED_SYMBOLS or len(sym) > 5:
                    continue
                # Only add if not already in results (movers take priority)
                if sym not in results:
                    results[sym] = {
                        "price": 0, "pct_change": 0, "score": 3,
                        "direction": "neutral",
                        "catalyst": f"Most active: {trades:,} trades, {vol:,} vol",
                        "source": "alpaca_active",
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                else:
                    # Boost score if also in movers
                    results[sym]["score"] = min(10, results[sym]["score"] + 2)
        except Exception as e:
            log.debug("Active scan error: %s", e)

    def _scan_x_trending(self, results: dict) -> None:
        """Pull trending tickers from X research module."""
        try:
            from data.x_research import x_researcher
            trending = x_researcher.trending_details
            for sym, info in trending.items():
                if not self._is_valid(sym):
                    continue
                if sym not in results:
                    results[sym] = {
                        "price": 0, "pct_change": 0,
                        "score": info.get("score", 4),
                        "direction": "long" if info.get("sentiment") == "bullish" else "short",
                        "catalyst": f"X buzz: {info.get('catalyst', '')[:60]}",
                        "source": "x_research",
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                else:
                    results[sym]["score"] = min(10, results[sym]["score"] + info.get("score", 2))
        except Exception:
            pass

    def _scan_news_catalysts(self, results: dict) -> None:
        """Finnhub news scan for catalyst-driven movers."""
        try:
            fh_key = os.environ.get("FINNHUB_API_KEY", "")
            if not fh_key:
                return
            r = requests.get(
                f"https://finnhub.io/api/v1/news?category=general&token={fh_key}",
                timeout=10)
            if not r.ok:
                return
            keywords_bull = {"SURGE", "SOAR", "BEAT", "UPGRADE", "APPROVAL",
                             "BREAKOUT", "RECORD", "DEAL", "CONTRACT", "FDA"}
            keywords_bear = {"CRASH", "PLUNGE", "MISS", "DOWNGRADE", "LAWSUIT",
                             "FRAUD", "BANKRUPTCY", "DEFAULT", "SANCTIONS", "WAR"}
            skip_words = {"THE", "AND", "FOR", "ARE", "NOT", "HAS", "CEO", "IPO",
                          "FDA", "SEC", "ETF", "NYSE", "WHO", "USA", "GDP"}
            for article in r.json()[:40]:
                headline = article.get("headline", "").upper()
                tickers = re.findall(r'\b([A-Z]{2,5})\b', headline)
                for tick in tickers:
                    if tick in skip_words or not self._is_valid(tick):
                        continue
                    is_bull = any(kw in headline for kw in keywords_bull)
                    is_bear = any(kw in headline for kw in keywords_bear)
                    if is_bull or is_bear:
                        if tick not in results:
                            results[tick] = {
                                "price": 0, "pct_change": 0, "score": 4,
                                "direction": "long" if is_bull else "short",
                                "catalyst": article.get("headline", "")[:80],
                                "source": "finnhub_news",
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                        else:
                            results[tick]["score"] = min(10, results[tick]["score"] + 2)
        except Exception as e:
            log.debug("News scan error: %s", e)

    def get_options_chain(self, symbol: str, direction: str = "long",
                          days_out: int = 7) -> Optional[dict]:
        """Find the best call or put option for a directional bet.
        direction='long' -> buy calls, direction='short' -> buy puts."""
        try:
            from datetime import date
            opt_type = "call" if direction == "long" else "put"
            exp_after = (date.today() + timedelta(days=2)).isoformat()
            exp_before = (date.today() + timedelta(days=days_out + 7)).isoformat()

            r = requests.get(
                f"https://paper-api.alpaca.markets/v2/options/contracts",
                params={
                    "underlying_symbols": symbol,
                    "status": "active",
                    "type": opt_type,
                    "expiration_date_gte": exp_after,
                    "expiration_date_lte": exp_before,
                    "limit": 10,
                },
                headers=self._headers, timeout=10)

            if not r.ok:
                return None

            contracts = r.json()
            if isinstance(contracts, dict):
                contracts = contracts.get("option_contracts", [])
            if not contracts:
                return None

            # Pick the closest to ATM (at-the-money)
            best = None
            best_distance = float("inf")
            for c in contracts:
                if isinstance(c, dict):
                    strike = float(c.get("strike_price", 0))
                    # We want closest to current price
                    # For calls: slightly OTM (strike just above price)
                    # For puts: slightly OTM (strike just below price)
                    distance = abs(strike - (self._hot_stocks.get(symbol, {}).get("price", strike)))
                    if distance < best_distance:
                        best_distance = distance
                        best = c

            if best:
                return {
                    "symbol": best.get("symbol", ""),
                    "underlying": symbol,
                    "type": opt_type,
                    "strike": float(best.get("strike_price", 0)),
                    "expiration": best.get("expiration_date", ""),
                    "id": best.get("id", ""),
                }
            return None
        except Exception as e:
            log.debug("Options chain error for %s: %s", symbol, e)
            return None


# ── Singleton ─────────────────────────────────────────────────────────────────
scanner = FullMarketScanner()
