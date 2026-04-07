"""
X/Grok Research Module — Real-time market intelligence from X (Twitter)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses xAI's Grok API with live X search to find:
  • Trending stock tickers with momentum catalysts
  • Earnings surprises, FDA approvals, contract wins
  • Social sentiment spikes (retail buzz = momentum fuel)
  • Low-float stocks getting attention (squeeze candidates)

Also scrapes financial news APIs for gap/momentum setups.
"""
import json
import os
import re
import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from utils.logger import get_logger

log = get_logger("data.x_research")

# ── Config ────────────────────────────────────────────────────────────────────
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_MODEL = "grok-4-1-fast"  # Fast + cheap, good for research

# Research refresh interval
RESEARCH_INTERVAL = 300  # 5 minutes between scans
MIN_MENTIONS = 3  # Minimum mentions to consider a ticker "trending"


class XResearcher:
    """Uses Grok API with live X search to find explosive stock movers."""

    def __init__(self) -> None:
        self._api_key = XAI_API_KEY
        self._trending: Dict[str, dict] = {}
        self._last_scan: float = 0
        self._lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def trending_tickers(self) -> List[str]:
        """Return tickers sorted by buzz score."""
        with self._lock:
            sorted_t = sorted(
                self._trending.items(),
                key=lambda x: x[1].get("score", 0),
                reverse=True,
            )
            return [sym for sym, _ in sorted_t[:20]]

    @property
    def trending_details(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._trending)

    def needs_refresh(self) -> bool:
        return time.time() - self._last_scan > RESEARCH_INTERVAL

    def scan_async(self) -> None:
        """Non-blocking scan in background thread."""
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._scan_thread = threading.Thread(target=self._do_scan, daemon=True)
        self._scan_thread.start()

    def _do_scan(self) -> None:
        """Run the full research pipeline."""
        try:
            results = {}

            # 1. Ask Grok to search X for trending stock tickers
            if self.has_api_key:
                grok_picks = self._grok_search_x()
                for pick in grok_picks:
                    sym = pick.get("ticker", "").upper()
                    if sym and 1 <= len(sym) <= 5:
                        results[sym] = {
                            "score": pick.get("score", 5),
                            "catalyst": pick.get("catalyst", "X buzz"),
                            "sentiment": pick.get("sentiment", "bullish"),
                            "source": "grok_x",
                            "time": datetime.now(timezone.utc).isoformat(),
                        }

            # 2. Scan free financial APIs for movers
            api_movers = self._scan_free_apis()
            for sym, data in api_movers.items():
                if sym in results:
                    results[sym]["score"] += data.get("score", 3)
                else:
                    results[sym] = data

            with self._lock:
                self._trending = results
            self._last_scan = time.time()
            log.info("X Research: found %d trending tickers", len(results))

        except Exception as e:
            log.error("X Research scan failed: %s", e)

    def _grok_search_x(self) -> List[dict]:
        """Ask Grok to analyze X/Twitter for trending stocks with catalysts."""
        if not self._api_key:
            return []

        prompt = """You have access to live X (Twitter) search. Search X right now for:
1. Stock tickers trending in the last 2 hours ($TICKER format)
2. Stocks with sudden volume spikes or price breakouts being discussed
3. Earnings surprises, FDA approvals, contract wins, or other catalysts
4. Low-float or small-cap stocks getting unusual retail attention
5. Crypto tokens with momentum catalysts

Focus on stocks that could move 5-20%+ TODAY. Ignore already-pumped stocks.

Return ONLY a JSON array (no markdown, no explanation) like:
[
  {"ticker": "SYMBOL", "score": 8, "catalyst": "reason", "sentiment": "bullish"},
  {"ticker": "SYMBOL", "score": 6, "catalyst": "reason", "sentiment": "bearish"}
]

Score 1-10 (10 = highest conviction). Include 5-15 tickers.
Only return tradeable US stocks/ETFs (Alpaca-compatible). No OTC or foreign stocks."""

        try:
            resp = requests.post(
                f"{XAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a financial research analyst with live X search access. Return only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "search": {"mode": "auto"},  # Enable live X search
                },
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"```\w*\n?", "", content).strip()
            picks = json.loads(content)
            if isinstance(picks, list):
                log.info("Grok X search returned %d picks", len(picks))
                return picks
            return []

        except json.JSONDecodeError as e:
            log.warning("Grok returned non-JSON: %s", str(e)[:100])
            return []
        except requests.RequestException as e:
            log.warning("Grok API error: %s", str(e)[:100])
            return []
        except Exception as e:
            log.error("Grok search failed: %s", e)
            return []

    def _scan_free_apis(self) -> Dict[str, dict]:
        """Scan free financial APIs for today's biggest movers."""
        results = {}

        # 1. Alpaca most-active / top movers (free with paper account)
        try:
            alpaca_key = os.environ.get("ALPACA_API_KEY", "")
            alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
            if alpaca_key:
                headers = {
                    "APCA-API-KEY-ID": alpaca_key,
                    "APCA-API-SECRET-KEY": alpaca_secret,
                }
                # Top movers endpoint
                resp = requests.get(
                    "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives?by=trades&top=20",
                    headers=headers, timeout=10
                )
                if resp.ok:
                    data = resp.json()
                    for item in data.get("most_actives", []):
                        sym = item.get("symbol", "")
                        if sym:
                            results[sym] = {
                                "score": 5,
                                "catalyst": f"Most active: {item.get('trade_count', 0)} trades",
                                "sentiment": "neutral",
                                "source": "alpaca_screener",
                                "time": datetime.now(timezone.utc).isoformat(),
                            }

                # Top movers by % change
                resp2 = requests.get(
                    "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=20",
                    headers=headers, timeout=10
                )
                if resp2.ok:
                    data2 = resp2.json()
                    for item in data2.get("gainers", []):
                        sym = item.get("symbol", "")
                        pct = item.get("percent_change", 0)
                        if sym and abs(pct) > 3:
                            score = min(10, int(abs(pct) / 2) + 3)
                            results[sym] = {
                                "score": score,
                                "catalyst": f"Up {pct:.1f}% today",
                                "sentiment": "bullish",
                                "source": "alpaca_movers",
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
                    for item in data2.get("losers", []):
                        sym = item.get("symbol", "")
                        pct = item.get("percent_change", 0)
                        if sym and abs(pct) > 5:
                            score = min(10, int(abs(pct) / 2) + 2)
                            results[sym] = {
                                "score": score,
                                "catalyst": f"Down {pct:.1f}% — bounce candidate",
                                "sentiment": "bearish",
                                "source": "alpaca_movers",
                                "time": datetime.now(timezone.utc).isoformat(),
                            }
        except Exception as e:
            log.debug("Alpaca screener error: %s", e)

        # 2. Finnhub market news for catalyst detection (free tier)
        try:
            finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
            if finnhub_key:
                resp = requests.get(
                    f"https://finnhub.io/api/v1/news?category=general&token={finnhub_key}",
                    timeout=10,
                )
                if resp.ok:
                    for article in resp.json()[:30]:
                        headline = article.get("headline", "").upper()
                        # Extract tickers from headlines
                        tickers = re.findall(r'\b([A-Z]{2,5})\b', headline)
                        keywords_bull = ["SURGE", "SOAR", "BEAT", "UPGRADE", "APPROVAL", "BREAKOUT", "RECORD"]
                        keywords_bear = ["CRASH", "PLUNGE", "MISS", "DOWNGRADE", "LAWSUIT", "FRAUD"]
                        for tick in tickers:
                            if len(tick) < 2 or tick in ("THE", "AND", "FOR", "ARE", "NOT", "HAS", "CEO", "IPO", "FDA", "SEC", "ETF"):
                                continue
                            is_bull = any(kw in headline for kw in keywords_bull)
                            is_bear = any(kw in headline for kw in keywords_bear)
                            if is_bull or is_bear:
                                if tick not in results:
                                    results[tick] = {
                                        "score": 4,
                                        "catalyst": article.get("headline", "")[:80],
                                        "sentiment": "bullish" if is_bull else "bearish",
                                        "source": "finnhub_news",
                                        "time": datetime.now(timezone.utc).isoformat(),
                                    }
        except Exception as e:
            log.debug("Finnhub news error: %s", e)

        return results


    def get_analysis(self, symbol: str) -> Optional[dict]:
        """Ask Grok for a quick analysis of a specific stock."""
        if not self.has_api_key:
            return None

        prompt = f"""Analyze ${symbol} right now using live X data:
1. What's the current sentiment on X?
2. Any catalysts in the last 24 hours?
3. Key support/resistance levels mentioned by traders
4. Bull vs bear case in 1 sentence each
5. Conviction score 1-10 for a long trade today

Return JSON: {{"sentiment": "bullish/bearish/neutral", "score": N, "catalyst": "...", "support": N, "resistance": N, "bull_case": "...", "bear_case": "..."}}"""

        try:
            resp = requests.post(
                f"{XAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "search": {"mode": "auto"},
                },
                timeout=20,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"```\w*\n?", "", content).strip()
            return json.loads(content)
        except Exception as e:
            log.debug("Grok analysis for %s failed: %s", symbol, e)
            return None


# Singleton
x_researcher = XResearcher()
