from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RequestSample:
    """What happened to one request that reached the engine.

    outcome: ok | error | timeout | cancelled (the client went away before the end).
    ttft_s is only known for streamed answers. Durations include the time spent in the queue,
    because that is what the client experiences.
    """

    stream: bool
    outcome: str
    duration_s: float
    ttft_s: float | None
    prompt_tokens: int
    completion_tokens: int


class MetricsSink(Protocol):
    def request_finished(self, sample: RequestSample) -> None: ...

    def request_rejected(self, reason: str) -> None:
        """A request turned away before reaching the engine (unauthorized, rate_limited, ...)."""
        ...

    def queue_waited(self, seconds: float) -> None: ...


class NullMetrics:
    """Used when nobody is listening."""

    def request_finished(self, sample: RequestSample) -> None:
        pass

    def request_rejected(self, reason: str) -> None:
        pass

    def queue_waited(self, seconds: float) -> None:
        pass
