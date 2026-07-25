"""Rate limiting and retry for the free data path — stdlib only, no extra dependency.

Free public endpoints (yfinance) have no SLA: they rate-limit bursts and blip intermittently.
A batch scan fans out over a thread pool, so a *shared* `RateLimiter` paces the whole group,
and `retry` gives each individual call a few backed-off attempts before it degrades. Both are
deliberately tiny — the project stays dependency-lean (no tenacity/backoff).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Thread-safe minimum-interval gate: at most one `acquire()` per `min_interval` seconds.

    A single instance shared across scan threads paces them as a group. `min_interval <= 0`
    disables it (single-stock analysis pays essentially nothing).
    """

    def __init__(self, min_interval: float = 0.0) -> None:
        self._min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._next_at = 0.0  # monotonic time the next call is allowed

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._min_interval


def retry(
    fn: Callable[[], T],
    *,
    retries: int,
    backoff: float,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Call `fn()`, retrying on `exceptions` up to `retries` attempts with exponential backoff.

    `retries` is the total number of attempts (>=1). Sleeps `backoff * 2**attempt` between tries.
    Re-raises the last exception if every attempt fails.
    """
    attempts = max(1, retries)
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except exceptions as e:
            last = e
            if attempt < attempts - 1 and backoff > 0:
                time.sleep(backoff * 2**attempt)
    assert last is not None  # unreachable: attempts >= 1 so the loop ran and either returned or set last
    raise last
