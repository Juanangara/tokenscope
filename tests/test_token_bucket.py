import pytest

from app.adapters.outbound.token_bucket import TokenBucketRateLimiter, UnlimitedRateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


def limiter(clock, per_minute=60, burst=3) -> TokenBucketRateLimiter:
    return TokenBucketRateLimiter(per_minute, burst, clock=clock)


def test_a_new_client_can_use_the_whole_burst_then_is_refused(clock):
    bucket = limiter(clock, burst=3)

    assert [bucket.check("a").allowed for _ in range(4)] == [True, True, True, False]


def test_a_refusal_says_how_long_to_wait(clock):
    bucket = limiter(clock, per_minute=60, burst=1)  # one token per second
    bucket.check("a")

    decision = bucket.check("a")

    assert not decision.allowed
    assert decision.retry_after_s == pytest.approx(1.0)


def test_waiting_gives_the_budget_back(clock):
    bucket = limiter(clock, per_minute=60, burst=1)
    bucket.check("a")
    assert not bucket.check("a").allowed

    clock.advance(1)

    assert bucket.check("a").allowed


def test_partial_waiting_shortens_the_retry_time(clock):
    bucket = limiter(clock, per_minute=60, burst=1)
    bucket.check("a")
    clock.advance(0.25)

    assert bucket.check("a").retry_after_s == pytest.approx(0.75)


def test_the_bucket_never_holds_more_than_the_burst(clock):
    bucket = limiter(clock, burst=2)
    clock.advance(3600)  # a long idle time must not bank unlimited requests

    assert [bucket.check("a").allowed for _ in range(3)] == [True, True, False]


def test_clients_have_separate_budgets(clock):
    bucket = limiter(clock, burst=1)
    bucket.check("a")

    assert not bucket.check("a").allowed
    assert bucket.check("b").allowed


def test_refused_requests_do_not_dig_a_deeper_hole(clock):
    bucket = limiter(clock, per_minute=60, burst=1)
    bucket.check("a")
    for _ in range(10):
        bucket.check("a")  # hammering while empty

    clock.advance(1)

    assert bucket.check("a").allowed


@pytest.mark.parametrize("per_minute, burst", [(0, 1), (-5, 1), (60, 0)])
def test_invalid_configuration_is_rejected(per_minute, burst):
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(per_minute, burst)


def test_unlimited_limiter_always_allows():
    unlimited = UnlimitedRateLimiter()

    assert all(unlimited.check("a").allowed for _ in range(1000))
