from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

Clock = Callable[[], float]


@dataclass(frozen=True)
class Decision:
    """Result of one rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: float  # seconds until the next request could succeed; 0 if allowed
    reset_after: float  # seconds until the caller is back to a full quota


class Limiter(Protocol):
    def check(self, key: str, cost: int = 1) -> Decision: ...


class KeyedLocks:
    """One lock per key so unrelated keys never contend with each other."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def get(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock


def default_clock() -> float:
    return time.monotonic()
