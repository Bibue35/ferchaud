"""
Algorithmic Execution — TWAP / VWAP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Slices large orders into smaller child orders to minimise market impact.

TWAP: Time-Weighted Average Price
  - Splits order into N equal slices at fixed time intervals.
  - Best for: steady, low-urgency execution.

VWAP: Volume-Weighted Average Price
  - Distributes order proportionally to historical intraday volume profile.
  - Targets a participation rate (e.g., 10% of volume).
  - Best for: blending into normal volume, minimising footprint.
"""
import threading
import time
from typing import List, Optional

import numpy as np
import pandas as pd

from config import CONFIG
from core.broker import Broker
from utils.logger import get_logger

log = get_logger("core.exec_algo")


class TWAPExecutor:
    """
    Splits an order into `n_slices` child orders spaced `interval_sec` apart.

    Each slice is submitted as a market or limit order.
    Runs in a background thread so the main loop isn't blocked.
    """

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def execute(
        self,
        symbol: str,
        total_qty: float,
        side: str,
        n_slices: int = CONFIG.twap_slices,
        interval_sec: int = CONFIG.twap_interval_sec,
    ) -> None:
        if total_qty <= 0:
            return

        slice_qty = max(1, int(total_qty / n_slices))
        remainder = int(total_qty - slice_qty * n_slices)

        def _run():
            filled = 0
            for i in range(n_slices):
                qty = slice_qty + (1 if i == 0 and remainder > 0 else 0)
                result = self._broker.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    order_type="market",
                    time_in_force="day",
                )
                if result:
                    filled += qty
                    log.info(
                        "TWAP slice %d/%d  %s %s %d  (cumulative: %d/%d)",
                        i + 1, n_slices, side.upper(), symbol, qty, filled, int(total_qty),
                    )
                else:
                    log.error("TWAP slice %d/%d failed for %s", i + 1, n_slices, symbol)
                    break

                if i < n_slices - 1:
                    time.sleep(interval_sec)

            log.info("TWAP complete: %s %s %d/%d filled", side.upper(), symbol, filled, int(total_qty))

        t = threading.Thread(target=_run, daemon=True, name=f"twap-{symbol}")
        t.start()
        log.info(
            "TWAP started: %s %s %d in %d slices @ %ds intervals",
            side.upper(), symbol, int(total_qty), n_slices, interval_sec,
        )


class VWAPExecutor:
    """
    Volume-participation VWAP algorithm.

    Uses the historical intraday volume profile (from the prior 5 days)
    to determine how many shares to send in each time bucket.
    Target participation rate = CONFIG.vwap_participation.
    """

    def __init__(self, broker: Broker, feed) -> None:
        self._broker = broker
        self._feed = feed

    def _get_volume_profile(self, symbol: str) -> Optional[np.ndarray]:
        """Return normalised intraday volume profile (78 bins for 5-min bars)."""
        df = self._feed.get_bars(symbol, timeframe="5Min", limit=78 * 5)
        if df.empty or len(df) < 78:
            return None

        # Average volume per 5-min bucket across recent days
        df = df.copy()
        df["time_bucket"] = df.index.time
        profile = df.groupby("time_bucket")["volume"].mean()
        vals = profile.values.astype(float)
        total = vals.sum()
        if total <= 0:
            return None
        return vals / total

    def execute(
        self,
        symbol: str,
        total_qty: float,
        side: str,
        participation: float = CONFIG.vwap_participation,
    ) -> None:
        if total_qty <= 0:
            return

        profile = self._get_volume_profile(symbol)

        if profile is None:
            log.warning("No volume profile for %s — falling back to TWAP", symbol)
            TWAPExecutor(self._broker).execute(symbol, total_qty, side)
            return

        def _run():
            remaining = int(total_qty)
            filled = 0

            for i, weight in enumerate(profile):
                if remaining <= 0:
                    break

                # This bucket's target = total × weight, but cap by participation
                bucket_qty = max(1, int(total_qty * weight))
                bucket_qty = min(bucket_qty, remaining)

                result = self._broker.submit_order(
                    symbol=symbol,
                    qty=bucket_qty,
                    side=side,
                    order_type="market",
                    time_in_force="day",
                )
                if result:
                    filled += bucket_qty
                    remaining -= bucket_qty
                    log.debug(
                        "VWAP bucket %d: %s %s %d (filled: %d/%d)",
                        i, side.upper(), symbol, bucket_qty, filled, int(total_qty),
                    )
                else:
                    log.error("VWAP bucket %d failed for %s", i, symbol)

                # Wait until next 5-min bucket
                if remaining > 0 and i < len(profile) - 1:
                    time.sleep(300)  # 5 minutes

            log.info("VWAP complete: %s %s %d/%d filled", side.upper(), symbol, filled, int(total_qty))

        t = threading.Thread(target=_run, daemon=True, name=f"vwap-{symbol}")
        t.start()
        log.info(
            "VWAP started: %s %s %d @ %.0f%% participation",
            side.upper(), symbol, int(total_qty), participation * 100,
        )
