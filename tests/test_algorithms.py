import threading

import pytest

from ratelimit import FixedWindow, SlidingWindowCounter, SlidingWindowLog, TokenBucket


def make_all(clock):
    return [
        FixedWindow(5, 10, clock),
        SlidingWindowLog(5, 10, clock),
        SlidingWindowCounter(5, 10, clock),
        TokenBucket(5, 0.5, clock),
    ]


def test_allows_up_to_limit_then_rejects(clock):
    for limiter in make_all(clock):
        results = [limiter.check("u").allowed for _ in range(7)]
        assert results == [True] * 5 + [False] * 2, type(limiter).__name__


def test_keys_are_independent(clock):
    for limiter in make_all(clock):
        for _ in range(5):
            assert limiter.check("a").allowed
        assert not limiter.check("a").allowed
        assert limiter.check("b").allowed


def test_remaining_counts_down(clock):
    for limiter in make_all(clock):
        assert [limiter.check("u").remaining for _ in range(5)] == [4, 3, 2, 1, 0], type(limiter).__name__


def test_retry_after_is_honest(clock):
    """Waiting exactly retry_after must be enough for the next request to pass."""
    for limiter in make_all(clock):
        for _ in range(5):
            limiter.check("u")
        rejected = limiter.check("u")
        assert not rejected.allowed and rejected.retry_after > 0
        clock.advance(rejected.retry_after + 1e-6)
        assert limiter.check("u").allowed, type(limiter).__name__


def test_fixed_window_boundary_burst_allows_double(clock):
    """Documents the known weakness: 2x the limit inside one window-length."""
    limiter = FixedWindow(5, 10, clock)
    clock.now = 1009.0  # 1 second before the boundary at 1010
    assert sum(limiter.check("u").allowed for _ in range(5)) == 5
    clock.advance(1.5)  # just past the boundary
    assert sum(limiter.check("u").allowed for _ in range(5)) == 5


def test_sliding_log_blocks_the_boundary_burst(clock):
    limiter = SlidingWindowLog(5, 10, clock)
    clock.now = 1009.0
    assert sum(limiter.check("u").allowed for _ in range(5)) == 5
    clock.advance(1.5)
    assert sum(limiter.check("u").allowed for _ in range(5)) == 0


def test_sliding_counter_limits_the_boundary_burst(clock):
    limiter = SlidingWindowCounter(5, 10, clock)
    clock.now = 1009.0
    assert sum(limiter.check("u").allowed for _ in range(5)) == 5
    clock.advance(1.5)  # 5% into the next window: previous window still weighs 95%
    assert sum(limiter.check("u").allowed for _ in range(5)) == 0


def test_token_bucket_refills_at_rate(clock):
    limiter = TokenBucket(capacity=4, rate=2.0, clock=clock)
    for _ in range(4):
        assert limiter.check("u").allowed
    assert not limiter.check("u").allowed
    clock.advance(1.0)  # 2 tokens back
    assert [limiter.check("u").allowed for _ in range(3)] == [True, True, False]
    clock.advance(60.0)  # never exceeds capacity
    assert sum(limiter.check("u").allowed for _ in range(10)) == 4


def test_cost_weighted_requests(clock):
    limiter = TokenBucket(capacity=10, rate=1.0, clock=clock)
    assert limiter.check("u", cost=7).allowed
    blocked = limiter.check("u", cost=5)
    assert not blocked.allowed
    assert blocked.retry_after == pytest.approx(2.0)


def test_invalid_arguments():
    with pytest.raises(ValueError):
        FixedWindow(0, 10)
    with pytest.raises(ValueError):
        TokenBucket(5, 0)
    with pytest.raises(ValueError):
        SlidingWindowLog(5, 10).check("u", cost=0)


@pytest.mark.parametrize("factory", [
    lambda: FixedWindow(100, 3600),
    lambda: SlidingWindowLog(100, 3600),
    lambda: SlidingWindowCounter(100, 3600),
    lambda: TokenBucket(100, 1e-9),
])
def test_never_over_admits_under_concurrency(factory):
    """16 threads hammer one key; exactly `limit` requests may pass."""
    limiter = factory()
    allowed = []
    start = threading.Barrier(16)

    def worker():
        start.wait()
        allowed.append(sum(limiter.check("shared").allowed for _ in range(50)))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(allowed) == 100
