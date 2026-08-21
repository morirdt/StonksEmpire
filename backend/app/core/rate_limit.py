"""A token bucket, held in process memory.

**This is per-process.** With more than one worker each gets its own bucket, so
the effective limit is the configured rate times the worker count. That is
acceptable for Phase 1 because the limits exist to blunt credential stuffing,
not to meter a paid API. Phase 5 introduces Redis and this moves behind it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class TokenBucketLimiter:
    """Allows ``capacity`` events per ``window_seconds``, refilling smoothly.

    A smooth refill rather than a fixed window avoids the burst that lets an
    attacker spend a full allowance on either side of a window boundary.
    """

    capacity: int
    window_seconds: float
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Consume one token for ``key``, returning False when exhausted."""
        moment = now if now is not None else time.monotonic()
        refill_rate = self.capacity / self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=self.capacity - 1, updated_at=moment)
                return True

            elapsed = max(0.0, moment - bucket.updated_at)
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * refill_rate)
            bucket.updated_at = moment

            if bucket.tokens < 1:
                return False
            bucket.tokens -= 1
            return True

    def time_until_token(self, key: str, *, now: float | None = None) -> float:
        """Seconds until ``key`` would be allowed again. ``0.0`` if it is now.

        Callers that must *wait* for capacity — the market data providers, which
        meter a quota rather than blunt an attack — use this to sleep exactly
        long enough instead of polling ``allow`` in a loop.
        """
        moment = now if now is not None else time.monotonic()
        refill_rate = self.capacity / self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0.0
            elapsed = max(0.0, moment - bucket.updated_at)
            tokens = min(self.capacity, bucket.tokens + elapsed * refill_rate)
            if tokens >= 1:
                return 0.0
            return (1 - tokens) / refill_rate

    def reset(self) -> None:
        """Drop all state. For tests, so one case cannot starve the next."""
        with self._lock:
            self._buckets.clear()
