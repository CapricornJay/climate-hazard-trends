"""Per-client token buckets.

In memory, so the limits are per process. That's fine for one small
instance; with several replicas this should move to Redis.
"""

import math
import time
from collections import OrderedDict

from fastapi import Request


class RateLimited(Exception):
    def __init__(self, retry_after: float, message: str = "Too many requests, please slow down."):
        super().__init__(message)
        self.retry_after = max(1, math.ceil(retry_after))
        self.message = message


class TokenBucket:
    def __init__(self, per_period: float, period_s: float, burst: float | None = None, max_keys: int = 50_000, clock=time.monotonic):
        self.rate = per_period / period_s
        self.burst = burst if burst is not None else per_period
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def take(self, key: str, cost: float = 1.0) -> float:
        """Spend `cost` tokens. Returns 0 if allowed, otherwise seconds until it would be."""
        now = self.clock()
        tokens, last = self._buckets.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        wait = 0.0
        if tokens >= cost:
            tokens -= cost
        else:
            wait = (cost - tokens) / self.rate
        self._buckets[key] = (tokens, now)
        if len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)  # forget the least recently seen client
        return wait


def client_ip(request: Request, trusted_hops: int, header: str = "") -> str:
    """Only believe forwarding headers we've been told to trust; otherwise
    anyone can dodge the limiter by sending their own X-Forwarded-For."""
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    if trusted_hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"
