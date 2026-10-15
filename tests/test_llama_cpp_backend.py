import json

import httpx
import pytest

from app.adapters.outbound.llama_cpp import LlamaCppBackend
from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatMessage, ChatRequest, GenerationParams

ENGINE_ANSWER = {
    "choices": [{"message": {"role": "assistant", "content": "Hello."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
}


def backend_with(handler) -> LlamaCppBackend:
    client = httpx.AsyncClient(
        base_url="http://engine", transport=httpx.MockTransport(handler)
    )
    return LlamaCppBackend(client)


def chat(**params) -> ChatRequest:
    return ChatRequest((ChatMessage("user", "hi"),), GenerationParams(**params))


async def test_maps_the_engine_answer():
    backend = backend_with(lambda request: httpx.Response(200, json=ENGINE_ANSWER))

    result = await backend.generate(chat())

    assert result.content == "Hello."
    assert result.finish_reason == "stop"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (12, 3)


async def test_sends_the_parameters_to_the_engine():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=ENGINE_ANSWER)

    await backend_with(handler).generate(chat(max_tokens=9, temperature=0.2, stop=("END",)))

    assert sent["max_tokens"] == 9
    assert sent["temperature"] == 0.2
    assert sent["stop"] == ["END"]
    assert sent["stream"] is False
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def sse_response(*events: dict | str) -> httpx.Response:
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    body = "".join(f"data: {line}\n\n" for line in lines)
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def delta(text=None, finish=None) -> dict:
    return {"choices": [{"delta": {"content": text} if text else {}, "finish_reason": finish}]}


async def test_stream_maps_deltas_finish_reason_and_usage():
    usage_chunk = {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}}
    backend = backend_with(lambda request: sse_response(
        delta("Hel"), delta("lo"), delta(finish="stop"), usage_chunk, "[DONE]"))

    chunks = [c async for c in backend.stream(chat())]

    assert [c.delta for c in chunks] == ["Hel", "lo", "", ""]
    assert chunks[2].finish_reason == "stop"
    assert chunks[3].usage.completion_tokens == 2


async def test_stream_requests_streaming_with_usage():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return sse_response("[DONE]")

    [c async for c in backend_with(handler).stream(chat())]

    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}


async def test_stream_stops_at_done_marker():
    backend = backend_with(lambda request: sse_response(delta("a"), "[DONE]", delta("ignored")))

    chunks = [c async for c in backend.stream(chat())]

    assert [c.delta for c in chunks] == ["a"]


async def test_stream_engine_error_status_becomes_backend_unavailable():
    backend = backend_with(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(BackendUnavailable):
        [c async for c in backend.stream(chat())]


async def test_stream_unreachable_engine_becomes_backend_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(BackendUnavailable):
        [c async for c in backend_with(handler).stream(chat())]


async def test_ready_when_the_engine_health_check_passes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "ok"})

    assert await backend_with(handler).is_ready() is True
    assert seen["path"] == "/health"


async def test_not_ready_while_the_model_is_loading():
    backend = backend_with(lambda request: httpx.Response(503, json={"error": "loading"}))

    assert await backend.is_ready() is False


async def test_not_ready_when_the_engine_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    assert await backend_with(handler).is_ready() is False


async def test_engine_error_status_becomes_backend_unavailable():
    backend = backend_with(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(BackendUnavailable):
        await backend.generate(chat())


async def test_unreachable_engine_becomes_backend_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(BackendUnavailable):
        await backend_with(handler).generate(chat())


async def test_unexpected_payload_becomes_backend_unavailable():
    backend = backend_with(lambda request: httpx.Response(200, json={"oops": True}))

    with pytest.raises(BackendUnavailable):
        await backend.generate(chat())
