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
