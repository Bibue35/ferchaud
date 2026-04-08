"""Smart rate limiter that tracks API calls and backs off intelligently."""
import time
import threading
from collections import defaultdict

class SmartRateLimiter:
    def __init__(self, calls_per_minute=180):
        self.calls_per_minute = calls_per_minute
        self.call_log = []
        self.lock = threading.Lock()
        self.backoff_until = 0
    
    def wait_if_needed(self):
        """Wait if we're close to rate limit."""
        with self.lock:
            now = time.time()
            
            # If we're in backoff period, wait
            if now < self.backoff_until:
                wait = self.backoff_until - now
                time.sleep(wait)
                return
            
            # Clean old entries (older than 60s)
            self.call_log = [t for t in self.call_log if now - t < 60]
            
            # If approaching limit, slow down
            if len(self.call_log) >= self.calls_per_minute * 0.85:
                time.sleep(0.5)
            elif len(self.call_log) >= self.calls_per_minute * 0.95:
                time.sleep(2.0)
            
            self.call_log.append(now)
    
    def report_429(self):
        """Called when we get a 429 response. Apply exponential backoff."""
        with self.lock:
            self.backoff_until = time.time() + 5  # Back off for 5 seconds


rate_limiter = SmartRateLimiter()
