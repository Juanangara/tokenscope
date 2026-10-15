import json
from collections.abc import AsyncIterator

import httpx

from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatChunk, ChatRequest, ChatResult, Usage


class LlamaCppBackend:
    """InferenceBackend that talks to a llama.cpp server through its OpenAI-compatible API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @staticmethod
    def _payload(request: ChatRequest, stream: bool) -> dict:
        params = request.params
        payload: dict = {
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "stream": stream,
        }
        if params.stop:
            payload["stop"] = list(params.stop)
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def generate(self, request: ChatRequest) -> ChatResult:
        try:
            response = await self._client.post(
                "/v1/chat/completions", json=self._payload(request, stream=False)
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            usage = data["usage"]
            return ChatResult(
                content=choice["message"]["content"],
                finish_reason=choice.get("finish_reason") or "stop",
                usage=Usage(usage["prompt_tokens"], usage["completion_tokens"]),
            )
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise BackendUnavailable(f"unexpected answer from the engine: {exc}") from exc

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        # Leaving the `async with` (normally, on error, or because the consumer closed this
        # generator) closes the connection, and the engine stops generating.
        try:
            async with self._client.stream(
                "POST", "/v1/chat/completions", json=self._payload(request, stream=True)
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    chunk = self._parse_chunk(json.loads(data))
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise BackendUnavailable(f"unexpected answer from the engine: {exc}") from exc

    @staticmethod
    def _parse_chunk(data: dict) -> ChatChunk | None:
        choices = data.get("choices") or []
        delta = ""
        finish_reason = None
        if choices:
            delta = choices[0].get("delta", {}).get("content") or ""
            finish_reason = choices[0].get("finish_reason")

        usage = None
        if data.get("usage"):
            usage = Usage(data["usage"]["prompt_tokens"], data["usage"]["completion_tokens"])

        if not delta and finish_reason is None and usage is None:
            return None
        return ChatChunk(delta, finish_reason, usage)

    @staticmethod
    def _unavailable(exc: httpx.HTTPError) -> BackendUnavailable:
        detail = str(exc) or type(exc).__name__
        return BackendUnavailable(f"inference engine error: {detail}")
