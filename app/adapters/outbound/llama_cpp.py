import httpx

from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatRequest, ChatResult, Usage


class LlamaCppBackend:
    """InferenceBackend that talks to a llama.cpp server through its OpenAI-compatible API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def generate(self, request: ChatRequest) -> ChatResult:
        params = request.params
        payload: dict = {
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "stream": False,
        }
        if params.stop:
            payload["stop"] = list(params.stop)

        try:
            response = await self._client.post("/v1/chat/completions", json=payload)
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
            detail = str(exc) or type(exc).__name__
            raise BackendUnavailable(f"inference engine error: {detail}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise BackendUnavailable(f"unexpected answer from the engine: {exc}") from exc
