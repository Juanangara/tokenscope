import pytest

from app.domain.limits import InvalidRequest, RequestLimits
from app.domain.models import ChatMessage, ChatRequest, GenerationParams

LIMITS = RequestLimits(max_prompt_chars=50, max_tokens_limit=100)


def request(content="hi", role="user", **params) -> ChatRequest:
    return ChatRequest((ChatMessage(role, content),), GenerationParams(**params))


def test_valid_request_passes():
    LIMITS.validate(request())


def test_empty_messages_are_rejected():
    with pytest.raises(InvalidRequest, match="empty"):
        LIMITS.validate(ChatRequest(messages=()))


def test_unknown_role_is_rejected():
    with pytest.raises(InvalidRequest, match="role"):
        LIMITS.validate(request(role="robot"))


def test_blank_prompt_is_rejected():
    with pytest.raises(InvalidRequest, match="text"):
        LIMITS.validate(request(content=""))


def test_prompt_over_the_limit_is_rejected():
    with pytest.raises(InvalidRequest, match="too long"):
        LIMITS.validate(request(content="x" * 51))


@pytest.mark.parametrize("max_tokens", [0, -1, 101])
def test_max_tokens_out_of_range_is_rejected(max_tokens):
    with pytest.raises(InvalidRequest, match="max_tokens"):
        LIMITS.validate(request(max_tokens=max_tokens))


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_temperature_out_of_range_is_rejected(temperature):
    with pytest.raises(InvalidRequest, match="temperature"):
        LIMITS.validate(request(temperature=temperature))


@pytest.mark.parametrize("top_p", [0.0, 1.5])
def test_top_p_out_of_range_is_rejected(top_p):
    with pytest.raises(InvalidRequest, match="top_p"):
        LIMITS.validate(request(top_p=top_p))


def test_too_many_stop_sequences_are_rejected():
    with pytest.raises(InvalidRequest, match="stop"):
        LIMITS.validate(request(stop=("a", "b", "c", "d", "e")))
