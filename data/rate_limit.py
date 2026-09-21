"""Token bucket for the shared outbound Sectors API client.

See idx_agent_infrastructure_diagrams_md.md section 12 and
sectors_idx_ingest_cache_plan_md.md section 6.
"""

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_per_second = refill_per_second
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
        self._last_refill = now

    def acquire(self, tokens: int = 1, timeout: float | None = None) -> None:
        """Block until `tokens` are available, or raise TimeoutError."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait_for = (tokens - self._tokens) / self._refill_per_second
            if deadline is not None and time.monotonic() + wait_for > deadline:
                raise TimeoutError("timed out waiting for rate limit tokens")
            time.sleep(min(wait_for, 0.5))
