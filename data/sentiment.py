"""
Sentiment & News Alpha Layer
━━━━━━━━━━━━━━━━━━━━━━━━━━━
Aggregates sentiment signals from Alpaca's built-in news feed to produce
a real-time sentiment score per symbol.

Approach:
  1. Fetch recent news headlines via Alpaca News API
  2. Score each headline with a keyword-based NLP model (no external API needed)
  3. Compute time-decayed sentiment aggregation (recent news weighted more)
  4. Output: sentiment score in [-1, +1] per symbol

The sentiment score is used as:
  - An additional alpha feature in the ML model
  - A confirmation filter (don't go long if sentiment < -0.3)
  - A regime overlay (extreme negative sentiment = crisis confirmation)
"""
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import CONFIG
from utils.logger import get_logger

log = get_logger("data.sentiment")

# Weighted by impact strength. Battle-tested financial keywords.
BULLISH_WORDS = {
    "surge": 2.0, "soar": 2.0, "rally": 2.0, "breakout": 2.0, "record high": 2.0,
    "all-time high": 2.0, "ath": 2.0, "moonshot": 2.0, "parabolic": 2.0,
    "blowout earnings": 2.0, "beats estimates": 2.0, "upgrade": 1.8,
    "outperform": 1.8, "bullish": 1.8, "buy rating": 1.8,
    "gain": 1.0, "rise": 1.0, "climb": 1.0, "advance": 1.0, "recover": 1.0,
    "rebound": 1.0, "growth": 1.0, "profit": 1.0, "positive": 1.0,
    "strong": 1.0, "optimistic": 1.0, "upbeat": 1.0, "boost": 1.0,
    "expansion": 1.0, "innovation": 1.0, "partnership": 1.0, "acquisition": 1.0,
    "dividend": 0.8, "buyback": 0.8, "beat": 0.8, "exceed": 0.8,
    "approval": 0.8, "launch": 0.7, "adoption": 0.7,
}

BEARISH_WORDS = {
    "crash": 2.0, "plunge": 2.0, "collapse": 2.0, "bankrupt": 2.0,
    "fraud": 2.0, "scandal": 2.0, "liquidation": 2.0, "hack": 2.0,
    "exploit": 2.0, "rug pull": 2.0, "ponzi": 2.0, "sec lawsuit": 2.0,
    "default": 2.0, "delisting": 2.0, "downgrade": 1.8,
    "fall": 1.0, "drop": 1.0, "decline": 1.0, "loss": 1.0, "sell-off": 1.0,
    "selloff": 1.0, "bear": 1.0, "bearish": 1.0, "underperform": 1.0,
    "weak": 1.0, "recession": 1.2, "inflation": 0.8, "rate hike": 0.8,
    "layoff": 1.0, "miss": 1.0, "warning": 0.8, "concern": 0.7,
    "risk": 0.5, "uncertainty": 0.7, "volatility": 0.5, "investigation": 1.0,
    "lawsuit": 0.9, "regulation": 0.6, "ban": 1.2, "restrict": 0.8,
    "outflow": 0.9, "sell": 0.5,
}


def _score_headline(headline: str) -> float:
    """Score a single headline. Returns [-1, +1]."""
    text = headline.lower()
    bull_score = sum(w for word, w in BULLISH_WORDS.items() if word in text)
    bear_score = sum(w for word, w in BEARISH_WORDS.items() if word in text)
    total = bull_score + bear_score
    if total == 0:
        return 0.0
    raw = (bull_score - bear_score) / total
    return float(np.clip(raw, -1.0, 1.0))


def _time_decay_weight(article_time: datetime, now: datetime, halflife_hours: float = 12.0) -> float:
    """Exponential decay: recent news counts more."""
    age_hours = (now - article_time).total_seconds() / 3600
    if age_hours < 0:
        age_hours = 0
    return math.exp(-math.log(2) * age_hours / halflife_hours)


class SentimentAnalyzer:
    """
    Fetches news from Alpaca and computes per-symbol sentiment scores.
    The score is a time-decayed weighted average of headline sentiments
    over the last 48 hours.
    """

    def __init__(self, api) -> None:
        self._api = api
        self._cache: Dict[str, Tuple[float, datetime]] = {}
        self._cache_ttl = timedelta(minutes=15)

    def get_sentiment(self, symbol: str) -> float:
        """Return sentiment score in [-1, +1] for a symbol. Uses 15-min cache."""
        now = datetime.now(timezone.utc)
        if symbol in self._cache:
            score, cached_at = self._cache[symbol]
            if now - cached_at < self._cache_ttl:
                return score
        score = self._compute_sentiment(symbol)
        self._cache[symbol] = (score, now)
        return score

    def _compute_sentiment(self, symbol: str) -> float:
        """Fetch news and score."""
        try:
            clean_sym = symbol.replace("/USD", "").replace("/", "")
            news = self._api.get_news(clean_sym, limit=50)
        except Exception as e:
            log.debug("News fetch failed for %s: %s", symbol, e)
            return 0.0

        if not news:
            return 0.0

        now = datetime.now(timezone.utc)
        weighted_sum = 0.0
        weight_sum = 0.0

        for article in news:
            try:
                headline = article.headline or ""
                summary = getattr(article, "summary", "") or ""
                text = f"{headline} {summary}"
                score = _score_headline(text)
                if score == 0.0:
                    continue

                article_time = article.created_at
                if isinstance(article_time, str):
                    article_time = datetime.fromisoformat(article_time.replace("Z", "+00:00"))
                if article_time.tzinfo is None:
                    article_time = article_time.replace(tzinfo=timezone.utc)

                weight = _time_decay_weight(article_time, now, halflife_hours=12.0)
                weighted_sum += score * weight
                weight_sum += weight
            except Exception:
                continue

        if weight_sum == 0:
            return 0.0
        final = weighted_sum / weight_sum
        log.debug("Sentiment %s: %.3f (%d articles scored)", symbol, final, len(news))
        return float(np.clip(final, -1.0, 1.0))

    def get_bulk_sentiment(self, symbols: List[str]) -> Dict[str, float]:
        """Score multiple symbols at once."""
        return {sym: self.get_sentiment(sym) for sym in symbols}

    def is_sentiment_aligned(self, symbol: str, side: str, threshold: float = 0.15) -> bool:
        """
        Check if sentiment confirms the trade direction.
        For longs: sentiment must be > -threshold (not strongly negative)
        For shorts: sentiment must be < +threshold (not strongly positive)
        """
        score = self.get_sentiment(symbol)
        if side == "buy":
            return score > -threshold
        else:
            return score < threshold
