import pytest

from app.adapters.outbound.token_bucket import TokenBucketRateLimiter, UnlimitedRateLimiter
from app.application.access import ANONYMOUS, AccessControl
from app.application.errors import RateLimited, Unauthorized
from app.config import parse_api_keys

KEYS = {"alice": "key-alice", "bob": "key-bob"}


def open_access() -> AccessControl:
    return AccessControl({}, UnlimitedRateLimiter())


def secured(limiter=None) -> AccessControl:
    return AccessControl(KEYS, limiter or UnlimitedRateLimiter())


def test_without_keys_everyone_is_anonymous():
    access = open_access()

    assert not access.auth_enabled
    assert access.authorize(None).id == ANONYMOUS
    assert access.authorize("Bearer whatever").id == ANONYMOUS


def test_a_valid_key_identifies_the_client():
    assert secured().authorize("Bearer key-alice").id == "alice"
    assert secured().authorize("Bearer key-bob").id == "bob"


def test_the_bearer_scheme_is_case_insensitive():
    assert secured().authorize("bearer key-alice").id == "alice"


@pytest.mark.parametrize(
    "header, message",
    [
        (None, "missing"),
        ("", "missing"),
        ("Bearer wrong", "invalid"),
        ("Bearer key-alic", "invalid"),
        ("Bearer ", "malformed"),
        ("Basic key-alice", "malformed"),
        ("key-alice", "malformed"),
    ],
)
def test_bad_credentials_are_rejected(header, message):
    with pytest.raises(Unauthorized, match=message):
        secured().authorize(header)


def test_each_client_spends_its_own_budget():
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1)
    access = secured(limiter)

    access.authorize("Bearer key-alice")
    with pytest.raises(RateLimited):
        access.authorize("Bearer key-alice")

    assert access.authorize("Bearer key-bob").id == "bob"


def test_rate_limit_error_carries_the_wait_time():
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1)
    access = secured(limiter)
    access.authorize("Bearer key-alice")

    with pytest.raises(RateLimited) as error:
        access.authorize("Bearer key-alice")

    assert error.value.retry_after_s == pytest.approx(1.0, abs=0.05)
    assert "1s" in str(error.value)


def test_wrong_keys_do_not_spend_anyones_budget():
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1)
    access = secured(limiter)

    for _ in range(5):
        with pytest.raises(Unauthorized):
            access.authorize("Bearer wrong")

    assert access.authorize("Bearer key-alice").id == "alice"


def test_anonymous_callers_share_one_budget():
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1)
    access = AccessControl({}, limiter)

    access.authorize(None)
    with pytest.raises(RateLimited):
        access.authorize(None)


class TestParseApiKeys:
    def test_empty_means_no_keys(self):
        assert parse_api_keys("") == {}
        assert parse_api_keys("  ,  ") == {}

    def test_named_keys(self):
        assert parse_api_keys("alice:k1, bob:k2") == {"alice": "k1", "bob": "k2"}

    def test_unnamed_keys_get_a_position_based_name(self):
        assert parse_api_keys("k1,k2") == {"client-1": "k1", "client-2": "k2"}

    def test_a_key_may_contain_colons_and_equals(self):
        assert parse_api_keys("alice:ab:cd==") == {"alice": "ab:cd=="}

    @pytest.mark.parametrize("raw", ["alice:", ":key", "a:k,a:other", "a:same,b:same"])
    def test_invalid_entries_are_rejected(self, raw):
        with pytest.raises(ValueError):
            parse_api_keys(raw)
