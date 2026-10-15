import time
from collections.abc import Callable

from app.application.ports.rate_limiter import RateLimitDecision


class TokenBucketRateLimiter:
    """In-memory token bucket per client.

    Each client has a bucket holding up to `burst` requests that refills at
    `requests_per_minute`. A request spends one token; with none left it is refused and told
    how long until the next one. Runs on a single event loop, so no locking is needed.
    """

    def __init__(self, requests_per_minute: float, burst: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if requests_per_minute <= 0 or burst < 1:
            raise ValueError("requests_per_minute must be positive and burst at least 1")
        self._rate_per_s = requests_per_minute / 60
        self._burst = burst
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # client -> (tokens, last update)

    def check(self, client_id: str) -> RateLimitDecision:
        now = self._clock()
        tokens, last = self._buckets.get(client_id, (float(self._burst), now))
        tokens = min(float(self._burst), tokens + (now - last) * self._rate_per_s)

        if tokens >= 1:
            self._buckets[client_id] = (tokens - 1, now)
            return RateLimitDecision(allowed=True)

        self._buckets[client_id] = (tokens, now)
        return RateLimitDecision(allowed=False, retry_after_s=(1 - tokens) / self._rate_per_s)


class UnlimitedRateLimiter:
    """Used when rate limiting is turned off."""

    def check(self, client_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True)
