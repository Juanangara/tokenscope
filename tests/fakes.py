import asyncio
from collections.abc import AsyncIterator

from app.application.ports.inference_backend import BackendUnavailable
from app.application.ports.metrics import RequestSample
from app.domain.models import ChatChunk, ChatRequest, ChatResult, Usage


class RecordingMetrics:
    """MetricsSink that just remembers everything it was told."""

    def __init__(self) -> None:
        self.samples: list[RequestSample] = []
        self.rejections: list[str] = []
        self.queue_waits: list[float] = []

    def request_finished(self, sample: RequestSample) -> None:
        self.samples.append(sample)

    def request_rejected(self, reason: str) -> None:
        self.rejections.append(reason)

    def queue_waited(self, seconds: float) -> None:
        self.queue_waits.append(seconds)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    """In-memory InferenceBackend: no model, and it remembers what happened to it.

    - `tokens` is what a stream yields; `fail_after` breaks a stream after that many tokens;
    - `error` is raised instead of answering; `fail=True` is a shortcut for BackendUnavailable;
    - `delay` is the time each answer (or each streamed token) takes;
    - `gate` holds every request until it is set, to pile requests up on purpose;
    - `active` / `max_active` count requests being served now / at the same time at most;
    - `stream_closed` tells whether the consumer closed the stream (generation was stopped).
    """

    def __init__(self, content: str = "hello", fail: bool = False,
                 tokens: tuple[str, ...] = ("hel", "lo"), fail_after: int | None = None,
                 error: Exception | None = None, delay: float = 0,
                 gate: asyncio.Event | None = None) -> None:
        self.content = content
        self.error = error or (BackendUnavailable("fake engine is down") if fail else None)
        self.tokens = tokens
        self.fail_after = fail_after
        self.delay = delay
        self.gate = gate
        self.requests: list[ChatRequest] = []
        self.stream_closed = False
        self.active = 0
        self.max_active = 0

    ready = True

    async def is_ready(self) -> bool:
        return self.ready

    def _enter(self, request: ChatRequest) -> None:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    async def generate(self, request: ChatRequest) -> ChatResult:
        self._enter(request)
        try:
            if self.error:
                raise self.error
            if self.gate:
                await self.gate.wait()
            await asyncio.sleep(self.delay)
            return ChatResult(self.content, "stop", Usage(prompt_tokens=7, completion_tokens=3))
        finally:
            self.active -= 1

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        self._enter(request)
        self.stream_closed = False
        try:
            if self.error:
                raise self.error
            if self.gate:
                await self.gate.wait()
            for sent, token in enumerate(self.tokens):
                if self.fail_after is not None and sent == self.fail_after:
                    raise BackendUnavailable("fake engine broke")
                await asyncio.sleep(self.delay)
                yield ChatChunk(delta=token)
            yield ChatChunk(finish_reason="stop", usage=Usage(7, len(self.tokens)))
        finally:
            self.active -= 1
            self.stream_closed = True
