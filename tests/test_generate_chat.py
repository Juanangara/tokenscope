import pytest

from app.application.ports.inference_backend import BackendUnavailable
from app.application.use_cases import GenerateChat, StreamChat
from app.domain.limits import InvalidRequest, RequestLimits
from app.domain.models import ChatMessage, ChatRequest, GenerationParams
from tests.fakes import FakeBackend

LIMITS = RequestLimits(max_prompt_chars=50, max_tokens_limit=100)


def chat(content="hi") -> ChatRequest:
    return ChatRequest((ChatMessage("user", content),))


async def collect(stream) -> list:
    return [chunk async for chunk in stream]


async def test_returns_the_backend_answer():
    backend = FakeBackend("hello there")
    result = await GenerateChat(backend, LIMITS)(chat())

    assert result.content == "hello there"
    assert result.usage.total_tokens == 10


async def test_default_max_tokens_never_exceeds_the_limit():
    backend = FakeBackend()

    await GenerateChat(backend, LIMITS)(chat())

    assert backend.requests[0].params.max_tokens == 100  # limit is 100, default would be 256


async def test_explicit_max_tokens_is_kept():
    backend = FakeBackend()
    request = ChatRequest((ChatMessage("user", "hi"),), GenerationParams(max_tokens=20))

    await GenerateChat(backend, LIMITS)(request)

    assert backend.requests[0].params.max_tokens == 20


async def test_invalid_request_never_reaches_the_backend():
    backend = FakeBackend()

    with pytest.raises(InvalidRequest):
        await GenerateChat(backend, LIMITS)(chat("x" * 51))

    assert backend.requests == []


async def test_backend_failure_is_propagated():
    with pytest.raises(BackendUnavailable):
        await GenerateChat(FakeBackend(fail=True), LIMITS)(chat())


async def test_stream_yields_every_piece_in_order():
    chunks = await collect(StreamChat(FakeBackend(tokens=("a", "b")), LIMITS)(chat()))

    assert [c.delta for c in chunks] == ["a", "b", ""]
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage.completion_tokens == 2


async def test_stream_fills_default_max_tokens():
    backend = FakeBackend()

    await collect(StreamChat(backend, LIMITS)(chat()))

    assert backend.requests[0].params.max_tokens == 100


async def test_invalid_stream_request_never_reaches_the_backend():
    backend = FakeBackend()

    with pytest.raises(InvalidRequest):
        await collect(StreamChat(backend, LIMITS)(chat("x" * 51)))

    assert backend.requests == []


async def test_closing_the_stream_closes_the_backend_stream():
    backend = FakeBackend(tokens=tuple("abcdef"))
    stream = StreamChat(backend, LIMITS)(chat())

    await anext(stream)
    await stream.aclose()

    assert backend.stream_closed
