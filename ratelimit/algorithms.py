"""The four algorithms.

All share the same contract: ``check(key, cost)`` is atomic per key and returns a
``Decision``. Time comes from an injectable clock so tests are deterministic.
"""
from __future__ import annotations

import math
from collections import deque

from .core import Clock, Decision, KeyedLocks, default_clock


class _Base:
    def __init__(self, limit: int, window: float, clock: Clock = default_clock) -> None:
        if limit <= 0 or window <= 0:
            raise ValueError("limit and window must be positive")
        self.limit = limit
        self.window = float(window)
        self._clock = clock
        self._locks = KeyedLocks()

    def _reject_oversized(self, cost: int) -> None:
        if cost <= 0:
            raise ValueError("cost must be positive")


class FixedWindow(_Base):
    """Counter that resets at fixed boundaries.

    O(1) memory per key. Weakness: a burst straddling a boundary can let through
    up to 2x the limit inside one window-length of real time.
    """

    def __init__(self, limit: int, window: float, clock: Clock = default_clock) -> None:
        super().__init__(limit, window, clock)
        self._state: dict[str, tuple[int, int]] = {}  # key -> (window index, count)

    def check(self, key: str, cost: int = 1) -> Decision:
        self._reject_oversized(cost)
        with self._locks.get(key):
            now = self._clock()
            index = int(now // self.window)
            stored_index, count = self._state.get(key, (index, 0))
            if stored_index != index:
                count = 0
            until_reset = (index + 1) * self.window - now
            if count + cost > self.limit:
                self._state[key] = (index, count)
                return Decision(False, self.limit, max(self.limit - count, 0), until_reset, until_reset)
            count += cost
            self._state[key] = (index, count)
            return Decision(True, self.limit, self.limit - count, 0.0, until_reset)


class SlidingWindowLog(_Base):
    """Keeps a timestamp per request. Exact, but O(limit) memory per key."""

    def __init__(self, limit: int, window: float, clock: Clock = default_clock) -> None:
        super().__init__(limit, window, clock)
        self._logs: dict[str, deque[float]] = {}

    def check(self, key: str, cost: int = 1) -> Decision:
        self._reject_oversized(cost)
        with self._locks.get(key):
            now = self._clock()
            log = self._logs.setdefault(key, deque())
            cutoff = now - self.window
            while log and log[0] <= cutoff:
                log.popleft()
            if len(log) + cost > self.limit:
                # The request fits once enough of the oldest entries have expired.
                need = len(log) + cost - self.limit
                retry = log[need - 1] + self.window - now if need <= len(log) else self.window
                reset = log[-1] + self.window - now if log else 0.0
                return Decision(False, self.limit, self.limit - len(log), max(retry, 0.0), max(reset, 0.0))
            log.extend([now] * cost)
            return Decision(True, self.limit, self.limit - len(log), 0.0, log[-1] + self.window - now)


class SlidingWindowCounter(_Base):
    """Two fixed-window counters blended by how far we are into the current window.

    O(1) memory like FixedWindow, but smooths the boundary burst. The estimate
    assumes the previous window's requests were evenly spread.
    """

    def __init__(self, limit: int, window: float, clock: Clock = default_clock) -> None:
        super().__init__(limit, window, clock)
        self._state: dict[str, tuple[int, int, int]] = {}  # key -> (index, current, previous)

    def check(self, key: str, cost: int = 1) -> Decision:
        self._reject_oversized(cost)
        with self._locks.get(key):
            now = self._clock()
            index = int(now // self.window)
            stored_index, current, previous = self._state.get(key, (index, 0, 0))
            if stored_index == index - 1:
                previous, current = current, 0
            elif stored_index != index:
                previous, current = 0, 0
            elapsed = (now - index * self.window) / self.window
            weight = 1.0 - elapsed
            estimated = previous * weight + current
            until_boundary = (index + 1) * self.window - now
            if estimated + cost > self.limit:
                self._state[key] = (index, current, previous)
                if previous > 0 and current + cost <= self.limit:
                    # Wait until the previous window's weight has decayed enough.
                    needed_weight = (self.limit - cost - current) / previous
                    retry = max((weight - needed_weight) * self.window, 0.0)
                elif cost <= self.limit:
                    # Not possible in this window. After the boundary this window's count
                    # becomes the "previous" one and must decay until the request fits.
                    decay = max(1.0 - (self.limit - cost) / current, 0.0) if current else 0.0
                    retry = until_boundary + decay * self.window
                else:
                    retry = math.inf
                remaining = max(int(math.floor(self.limit - estimated)), 0)
                return Decision(False, self.limit, remaining, retry, until_boundary + self.window)
            current += cost
            self._state[key] = (index, current, previous)
            remaining = max(int(math.floor(self.limit - (previous * weight + current))), 0)
            return Decision(True, self.limit, remaining, 0.0, until_boundary + self.window)


class TokenBucket:
    """Bucket of ``capacity`` tokens refilled at ``rate`` tokens per second.

    Allows bursts up to ``capacity`` while holding the long-run average to ``rate``.
    """

    def __init__(self, capacity: int, rate: float, clock: Clock = default_clock) -> None:
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity and rate must be positive")
        self.limit = capacity
        self.rate = float(rate)
        self._clock = clock
        self._locks = KeyedLocks()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last refill time)

    def check(self, key: str, cost: int = 1) -> Decision:
        if cost <= 0:
            raise ValueError("cost must be positive")
        with self._locks.get(key):
            now = self._clock()
            tokens, last = self._state.get(key, (float(self.limit), now))
            tokens = min(self.limit, tokens + (now - last) * self.rate)
            if tokens < cost:
                self._state[key] = (tokens, now)
                retry = (cost - tokens) / self.rate if cost <= self.limit else math.inf
                return Decision(False, self.limit, int(tokens), retry, (self.limit - tokens) / self.rate)
            tokens -= cost
            self._state[key] = (tokens, now)
            return Decision(True, self.limit, int(tokens), 0.0, (self.limit - tokens) / self.rate)
