import pytest

from app.application.ports.inference_backend import BackendUnavailable
from app.application.use_cases import GenerateChat, StreamChat
from app.domain.limits import InvalidRequest, RequestLimits
from app.domain.models import ChatMessage, ChatRequest, GenerationParams
from tests.fakes import FakeBackend, RecordingObserver

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


class TestPromptObservation:
    async def test_a_valid_request_is_observed_with_its_defaults_applied(self):
        observer = RecordingObserver()

        await GenerateChat(FakeBackend(), LIMITS, observer)(chat())

        assert len(observer.requests) == 1
        assert observer.requests[0].params.max_tokens == 100

    async def test_streamed_requests_are_observed_too(self):
        observer = RecordingObserver()

        await collect(StreamChat(FakeBackend(), LIMITS, observer)(chat()))

        assert len(observer.requests) == 1

    async def test_an_invalid_request_is_not_observed(self):
        observer = RecordingObserver()

        with pytest.raises(InvalidRequest):
            await GenerateChat(FakeBackend(), LIMITS, observer)(chat("x" * 51))
        with pytest.raises(InvalidRequest):
            await collect(StreamChat(FakeBackend(), LIMITS, observer)(chat("x" * 51)))

        assert observer.requests == []

    async def test_a_request_the_engine_then_refuses_was_still_demand(self):
        observer = RecordingObserver()

        with pytest.raises(BackendUnavailable):
            await GenerateChat(FakeBackend(fail=True), LIMITS, observer)(chat())

        assert len(observer.requests) == 1

    async def test_a_broken_observer_does_not_break_the_answer(self):
        observer = RecordingObserver(error=RuntimeError("bug"))

        result = await GenerateChat(FakeBackend("fine"), LIMITS, observer)(chat())
        chunks = await collect(StreamChat(FakeBackend(), LIMITS, observer)(chat()))

        assert result.content == "fine"
        assert chunks[-1].finish_reason == "stop"


async def test_closing_the_stream_closes_the_backend_stream():
    backend = FakeBackend(tokens=tuple("abcdef"))
    stream = StreamChat(backend, LIMITS)(chat())

    await anext(stream)
    await stream.aclose()

    assert backend.stream_closed
