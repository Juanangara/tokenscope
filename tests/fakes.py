import asyncio
from collections.abc import AsyncIterator

from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatChunk, ChatRequest, ChatResult, Usage


class FakeBackend:
    """In-memory InferenceBackend: no model, and it remembers what happened to it.

    `tokens` is what a stream yields; `fail_after` makes a stream break after that many tokens;
    `stream_closed` tells whether the consumer closed the stream (i.e. generation was stopped).
    """

    def __init__(self, content: str = "hello", fail: bool = False,
                 tokens: tuple[str, ...] = ("hel", "lo"), fail_after: int | None = None) -> None:
        self.content = content
        self.fail = fail
        self.tokens = tokens
        self.fail_after = fail_after
        self.requests: list[ChatRequest] = []
        self.stream_closed = False

    async def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if self.fail:
            raise BackendUnavailable("fake engine is down")
        return ChatResult(self.content, "stop", Usage(prompt_tokens=7, completion_tokens=3))

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self.requests.append(request)
        self.stream_closed = False
        try:
            if self.fail:
                raise BackendUnavailable("fake engine is down")
            for sent, token in enumerate(self.tokens):
                if self.fail_after is not None and sent == self.fail_after:
                    raise BackendUnavailable("fake engine broke")
                await asyncio.sleep(0)
                yield ChatChunk(delta=token)
            yield ChatChunk(finish_reason="stop", usage=Usage(7, len(self.tokens)))
        finally:
            self.stream_closed = True
