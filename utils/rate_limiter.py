"""Global rate limiter — shared across all Alpaca API consumers.
Ensures feed + scanner + broker combined stay under 150 req/min.
"""
import threading
import time

_lock = threading.Lock()
_timestamps: list = []
_MAX_REQUESTS = 145  # slightly under 150 to leave headroom
_WINDOW = 60.0


def throttle() -> None:
    """Block the calling thread until we're under the global rate limit."""
    with _lock:
        now = time.time()
        cutoff = now - _WINDOW
        # Prune old timestamps
        _timestamps[:] = [t for t in _timestamps if t > cutoff]
        if len(_timestamps) >= _MAX_REQUESTS:
            sleep_time = _timestamps[0] - cutoff + 0.1
            if sleep_time > 0:
                time.sleep(sleep_time)
        _timestamps.append(time.time())
