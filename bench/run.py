"""Benchmark the four algorithms on throughput and on boundary-burst accuracy.

    python -m bench.run            # prints a Markdown table
"""
from __future__ import annotations

import random
import time

from ratelimit import FixedWindow, SlidingWindowCounter, SlidingWindowLog, TokenBucket

LIMIT, WINDOW = 100, 60.0


def throughput(factory, checks: int = 200_000, keys: int = 1_000) -> float:
    limiter = factory(time.monotonic)
    names = [f"user-{i}" for i in range(keys)]
    rng = random.Random(7)
    order = [names[rng.randrange(keys)] for _ in range(checks)]
    start = time.perf_counter()
    for key in order:
        limiter.check(key)
    return checks / (time.perf_counter() - start)


def worst_window(factory) -> int:
    """Most requests admitted in any real 60 s span, under an adversarial client.

    The client knows the window length. Each minute it fires 150 requests in the
    last 2 s of the window and 150 more in the first 2 s of the next one.
    """
    now = [0.0]
    limiter = factory(lambda: now[0])
    attempts: list[float] = []
    for minute in range(1, 60):
        boundary = minute * WINDOW
        attempts += [boundary - 2.0 + i * (2.0 / 150) for i in range(150)]
        attempts += [boundary + 0.001 + i * (2.0 / 150) for i in range(150)]
    admitted: list[float] = []
    for t in sorted(attempts):
        now[0] = t
        if limiter.check("client").allowed:
            admitted.append(t)
    worst, lo = 0, 0
    for hi, ts in enumerate(admitted):
        while admitted[lo] <= ts - WINDOW:
            lo += 1
        worst = max(worst, hi - lo + 1)
    return worst


def main() -> None:
    algos = {
        "Fixed window": lambda c: FixedWindow(LIMIT, WINDOW, c),
        "Sliding window log": lambda c: SlidingWindowLog(LIMIT, WINDOW, c),
        "Sliding window counter": lambda c: SlidingWindowCounter(LIMIT, WINDOW, c),
        "Token bucket": lambda c: TokenBucket(LIMIT, LIMIT / WINDOW, c),
    }
    print(f"Limit: {LIMIT} requests per {WINDOW:.0f} s. Adversarial client bursting around every window boundary for 1 hour.\n")
    print("| Algorithm | Checks / second | Most admitted in any 60 s | Over limit |")
    print("|---|---:|---:|---:|")
    for name, factory in algos.items():
        ops = throughput(factory)
        worst = worst_window(factory)
        print(f"| {name} | {ops:,.0f} | {worst} | {max(worst - LIMIT, 0) / LIMIT:.0%} |")


if __name__ == "__main__":
    main()
