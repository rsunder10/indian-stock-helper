"""RateLimiter pacing + retry/backoff — no real sleeping, monkeypatched clock."""

from __future__ import annotations

import pytest

from indi_analyst.datasources import throttle
from indi_analyst.datasources.throttle import RateLimiter, retry


class FakeClock:
    """Deterministic stand-in for time.monotonic/sleep — sleeping just advances the clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    c = FakeClock()
    monkeypatch.setattr(throttle.time, "monotonic", c.monotonic)
    monkeypatch.setattr(throttle.time, "sleep", c.sleep)
    return c


def test_rate_limiter_paces_successive_calls(clock):
    rl = RateLimiter(min_interval=0.2)
    rl.acquire()  # first is free
    assert clock.slept == []
    rl.acquire()  # must wait the full interval since no wall time passed
    assert clock.slept == [pytest.approx(0.2)]


def test_rate_limiter_no_wait_when_interval_elapsed(clock):
    rl = RateLimiter(min_interval=0.2)
    rl.acquire()
    clock.now += 0.5  # more than the interval has passed on its own
    rl.acquire()
    assert clock.slept == []  # no artificial sleep needed


def test_rate_limiter_zero_interval_is_noop(clock):
    rl = RateLimiter(min_interval=0.0)
    for _ in range(5):
        rl.acquire()
    assert clock.slept == []


def test_retry_succeeds_after_transient_failures(clock):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return "ok"

    assert retry(flaky, retries=3, backoff=0.5, exceptions=(ConnectionError,)) == "ok"
    assert calls["n"] == 3
    assert clock.slept == [pytest.approx(0.5), pytest.approx(1.0)]  # 0.5*2^0, 0.5*2^1


def test_retry_reraises_after_exhausting_attempts(clock):
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        retry(always_fails, retries=2, backoff=0.1)
    assert calls["n"] == 2  # exactly `retries` attempts, no extra


def test_retry_does_not_catch_unlisted_exceptions(clock):
    def boom():
        raise KeyError("unexpected")

    with pytest.raises(KeyError):
        retry(boom, retries=3, backoff=0.1, exceptions=(ConnectionError,))
