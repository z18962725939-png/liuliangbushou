"""
Concurrency limiter and token-bucket rate limiter.

The concurrency limiter caps the number of simultaneous in-flight requests.
The rate limiter caps the number of requests per second (sliding window).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, AsyncIterator


class ConcurrencyLimiter:
    """Async semaphore-based concurrency limiter."""

    def __init__(self, max_concurrency: int = 5):
        self._sem = asyncio.Semaphore(max_concurrency)

    async def __aenter__(self):
        await self._sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._sem.release()

    async def run(self, coro: Any) -> Any:
        async with self._sem:
            return await coro


class RateLimiter:
    """Simple sliding-window rate limiter (requests per second)."""

    def __init__(self, rate: float = 0.0):
        """
        Args:
            rate: Maximum requests per second. 0 or negative disables limiting.
        """
        self._rate = rate
        self._timestamps: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return self._rate > 0

    async def acquire(self) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        window_start = now - 1.0
        while self._timestamps and self._timestamps[0] < window_start:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._rate:
            wait = 1.0 - (now - self._timestamps[0])
            if wait > 0:
                await asyncio.sleep(wait)
        self._timestamps.append(time.monotonic())

    async def run(self, coro: Any) -> Any:
        await self.acquire()
        return await coro


async def gather_with_concurrency(
    coros: list[Any],
    max_concurrency: int = 5,
    rate: float = 0.0,
) -> list[Any]:
    """
    Run a list of coroutines with concurrency and optional rate limiting.

    Returns results in the same order as *coros*.
    """
    sem = asyncio.Semaphore(max_concurrency)
    limiter = RateLimiter(rate)

    async def _wrapper(idx: int, coro: Any) -> tuple[int, Any]:
        await limiter.acquire()
        async with sem:
            try:
                result = await coro
                return idx, result
            except Exception as exc:
                return idx, exc

    pairs = await asyncio.gather(*[_wrapper(i, c) for i, c in enumerate(coros)])
    pairs.sort(key=lambda p: p[0])
    return [p[1] for p in pairs]
