"""Market data feed — historical bars + real-time quote streaming via Alpaca."""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd
import alpaca_trade_api as tradeapi

from config import CONFIG
from utils.logger import get_logger

log = get_logger("data.feed")

# Suppress noisy websocket retry logs from alpaca library
logging.getLogger("websocket").setLevel(logging.CRITICAL)
logging.getLogger("alpaca_trade_api.stream").setLevel(logging.CRITICAL)
logging.getLogger("alpaca_trade_api").setLevel(logging.WARNING)


def _is_crypto(symbol: str) -> bool:
    """Return True if symbol is a crypto pair (e.g. BTC/USD)."""
    return "/" in symbol


class MarketDataFeed:
    """Fetches historical OHLCV bars and streams live quotes/trades."""

    def __init__(self) -> None:
        self._api = tradeapi.REST(
            CONFIG.api_key,
            CONFIG.secret_key,
            CONFIG.base_url,
            api_version="v2",
        )
        # Enforce 25-second timeout on all Alpaca HTTP calls
        try:
            _orig = self._api._session.request
            def _timed_request(*a, **kw):
                if not kw.get("timeout"):  # Override None or missing
                    kw["timeout"] = 10
                return _orig(*a, **kw)
            self._api._session.request = _timed_request
        except Exception:
            pass
        self._quote_callbacks: Dict[str, List[Callable]] = {}
        self._trade_callbacks: Dict[str, List[Callable]] = {}
        self._stream_thread: Optional[threading.Thread] = None
        self._latest_quotes: Dict[str, dict] = {}
    def _throttle(self) -> None:
        """Block until we're under the global rate limit."""
        from utils.rate_limiter import throttle
        throttle()

    # ── Historical data ───────────────────────────────────────────────────────

    def _tf(self, timeframe: str):
        tf_map = {
            "1Min": tradeapi.TimeFrame.Minute,
            "5Min": tradeapi.TimeFrame(5, tradeapi.TimeFrameUnit.Minute),
            "15Min": tradeapi.TimeFrame(15, tradeapi.TimeFrameUnit.Minute),
            "1H": tradeapi.TimeFrame.Hour,
            "1D": tradeapi.TimeFrame.Day,
        }
        return tf_map.get(timeframe, tradeapi.TimeFrame.Minute)

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        limit: int = 500,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Return OHLCV DataFrame for *symbol*."""
        try:
            self._throttle()
            tf = self._tf(timeframe)
            if start is None:
                start = datetime.now(timezone.utc) - timedelta(days=5)
            if end is None:
                end = datetime.now(timezone.utc)

            if _is_crypto(symbol):
                bars = self._api.get_crypto_bars(
                    symbol, tf,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    limit=limit,
                ).df
            else:
                bars = self._api.get_bars(
                    symbol, tf,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    limit=limit,
                    adjustment="raw",
                    feed="iex",
                ).df

            if bars.empty:
                return pd.DataFrame()

            bars.index = pd.to_datetime(bars.index, utc=True)
            cols = ["open", "high", "low", "close", "volume"]
            for c in cols:
                if c not in bars.columns:
                    return pd.DataFrame()
            bars = bars[cols].dropna()
            return bars

        except Exception as exc:
            log.error("get_bars(%s): %s", symbol, exc)
            return pd.DataFrame()

    def get_multi_bars(
        self, symbols: List[str], timeframe: str = "1D", limit: int = 252
    ) -> Dict[str, pd.DataFrame]:
        result: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = self.get_bars(sym, timeframe=timeframe, limit=limit)
            if not df.empty:
                result[sym] = df
        return result

    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        try:
            self._throttle()
            if _is_crypto(symbol):
                quotes = self._api.get_latest_crypto_quotes(symbol)
                q = quotes.get(symbol)
                if q is None:
                    return None
                return {"bid": q.bp, "ask": q.ap, "bid_size": q.bs, "ask_size": q._raw.get("as", 0)}
            else:
                q = self._api.get_latest_quote(symbol)
                return {"bid": q.bp, "ask": q.ap, "bid_size": q.bs, "ask_size": q.ask_size}
        except Exception as exc:
            log.error("get_latest_quote(%s): %s", symbol, exc)
            return None

    def get_latest_trade(self, symbol: str) -> Optional[float]:
        try:
            self._throttle()
            if _is_crypto(symbol):
                trades = self._api.get_latest_crypto_trades(symbol)
                t = trades.get(symbol)
                if t is None:
                    return None
            else:
                t = self._api.get_latest_trade(symbol)
            return t.p
        except Exception as exc:
            log.error("get_latest_trade(%s): %s", symbol, exc)
            return None

    # ── Streaming ─────────────────────────────────────────────────────────────

    def subscribe_quotes(self, symbol: str, callback: Callable) -> None:
        self._quote_callbacks.setdefault(symbol, []).append(callback)

    def subscribe_trades(self, symbol: str, callback: Callable) -> None:
        self._trade_callbacks.setdefault(symbol, []).append(callback)

    def start_stream(self, symbols: List[str]) -> None:
        """Start WebSocket stream. Gracefully degrades if market is closed."""
        if self._stream_thread and self._stream_thread.is_alive():
            return

        stock_syms = [s for s in symbols if not _is_crypto(s)]
        crypto_syms = [s for s in symbols if _is_crypto(s)]
        total = len(stock_syms) + len(crypto_syms)
        log.info("Starting market data stream for %d symbols (%d stocks, %d crypto)",
                 total, len(stock_syms), len(crypto_syms))

        def _run():
            try:
                from alpaca_trade_api.stream import Stream
                stream = Stream(
                    CONFIG.api_key,
                    CONFIG.secret_key,
                    base_url=CONFIG.base_url,
                    data_feed="iex",
                )

                async def _on_quote(q):
                    sym = q.symbol
                    data = {"bid": q.bid_price, "ask": q.ask_price, "timestamp": q.timestamp}
                    self._latest_quotes[sym] = data
                    for cb in self._quote_callbacks.get(sym, []):
                        try:
                            cb(sym, data)
                        except Exception:
                            pass

                async def _on_trade(t):
                    sym = t.symbol
                    for cb in self._trade_callbacks.get(sym, []):
                        try:
                            cb(sym, {"price": t.price, "size": t.size})
                        except Exception:
                            pass

                for sym in stock_syms:
                    stream.subscribe_quotes(_on_quote, sym)
                    stream.subscribe_trades(_on_trade, sym)
                for sym in crypto_syms:
                    try:
                        stream.subscribe_crypto_quotes(_on_quote, sym)
                        stream.subscribe_crypto_trades(_on_trade, sym)
                    except Exception:
                        pass

                self._stream_running = True
                stream.run()
            except Exception as e:
                # Stream failed (market closed, auth issues, etc.) — not fatal
                log.warning("Stream connection failed: %s — bot continues with REST polling", e)
                self._stream_running = False

        self._stream_thread = threading.Thread(target=_run, daemon=True)
        self._stream_thread.start()

    def stop_stream(self) -> None:
        self._stream_running = False
        log.info("Market data stream stopped")

    def get_cached_quote(self, symbol: str) -> Optional[dict]:
        return self._latest_quotes.get(symbol)
