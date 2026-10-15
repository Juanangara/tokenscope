import time
from collections.abc import AsyncIterator, Callable

from app.application.errors import GenerationTimeout, Overloaded, QueueTimeout
from app.application.ports.inference_backend import BackendUnavailable, InferenceBackend
from app.application.ports.metrics import MetricsSink, RequestSample
from app.domain.models import ChatChunk, ChatRequest, ChatResult


class MeasuredBackend:
    """Decorator that times every request and reports it to a MetricsSink.

    It wraps the protected backend, so durations include the time spent waiting in the queue.
    Requests turned away (Overloaded, QueueTimeout) never reach the engine and are not
    recorded here; whoever turns them into a response counts them as rejections.
    """

    def __init__(self, inner: InferenceBackend, metrics: MetricsSink,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._metrics = metrics
        self._clock = clock

    async def is_ready(self) -> bool:
        return await self._inner.is_ready()

    async def generate(self, request: ChatRequest) -> ChatResult:
        started = self._clock()
        outcome: str | None = "cancelled"  # what it stays if we are cancelled mid-way
        usage = None
        try:
            result = await self._inner.generate(request)
            outcome, usage = "ok", result.usage
            return result
        except (Overloaded, QueueTimeout):
            outcome = None
            raise
        except GenerationTimeout:
            outcome = "timeout"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            if outcome:
                self._record(False, outcome, started, None, usage and usage.prompt_tokens,
                             usage and usage.completion_tokens)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        started = self._clock()
        outcome: str | None = "cancelled"  # the consumer closing the stream lands here
        ttft: float | None = None
        pieces = 0
        usage = None
        try:
            async for chunk in self._inner.stream(request):
                if chunk.delta:
                    pieces += 1
                    if ttft is None:
                        ttft = self._clock() - started
                if chunk.usage:
                    usage = chunk.usage
                yield chunk
            outcome = "ok"
        except (Overloaded, QueueTimeout):
            outcome = None
            raise
        except GenerationTimeout:
            outcome = "timeout"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            if outcome:
                # Without the engine's own count (cancelled streams), one piece ~ one token.
                completion = usage.completion_tokens if usage else pieces
                self._record(True, outcome, started, ttft, usage and usage.prompt_tokens,
                             completion)

    def _record(self, stream: bool, outcome: str, started: float, ttft: float | None,
                prompt_tokens: int | None, completion_tokens: int | None) -> None:
        self._metrics.request_finished(RequestSample(
            stream=stream,
            outcome=outcome,
            duration_s=self._clock() - started,
            ttft_s=ttft,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
        ))
