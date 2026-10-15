import pytest

from app.application.errors import GenerationTimeout, Overloaded, QueueTimeout
from app.application.measured_backend import MeasuredBackend
from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatChunk, ChatMessage, ChatRequest, ChatResult, GenerationParams, Usage
from tests.fakes import FakeClock, RecordingMetrics

REQUEST = ChatRequest((ChatMessage("user", "hi"),), GenerationParams(max_tokens=10))


class TimedEngine:
    """Engine stub that makes the fake clock move: the answer takes 0.5s before the first
    token, then 0.1s per token."""

    def __init__(self, clock: FakeClock, tokens=("a", "b", "c"), error: Exception | None = None,
                 report_usage: bool = True) -> None:
        self.clock = clock
        self.tokens = tokens
        self.error = error
        self.report_usage = report_usage

    async def is_ready(self) -> bool:
        return True

    async def generate(self, request):
        self.clock.advance(2.0)
        if self.error:
            raise self.error
        return ChatResult("abc", "stop", Usage(prompt_tokens=11, completion_tokens=3))

    async def stream(self, request):
        self.clock.advance(0.5)
        if self.error:
            raise self.error
        for token in self.tokens:
            yield ChatChunk(delta=token)
            self.clock.advance(0.1)
        usage = Usage(11, len(self.tokens)) if self.report_usage else None
        yield ChatChunk(finish_reason="stop", usage=usage)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def metrics():
    return RecordingMetrics()


def measured(engine, metrics, clock) -> MeasuredBackend:
    return MeasuredBackend(engine, metrics, clock=clock)


async def test_a_finished_answer_is_measured(clock, metrics):
    await measured(TimedEngine(clock), metrics, clock).generate(REQUEST)

    (sample,) = metrics.samples
    assert sample.outcome == "ok"
    assert sample.stream is False
    assert sample.duration_s == pytest.approx(2.0)
    assert sample.ttft_s is None  # unknown without streaming
    assert (sample.prompt_tokens, sample.completion_tokens) == (11, 3)


async def test_a_finished_stream_has_time_to_first_token(clock, metrics):
    backend = measured(TimedEngine(clock), metrics, clock)

    pieces = [c.delta async for c in backend.stream(REQUEST)]

    assert pieces == ["a", "b", "c", ""]
    (sample,) = metrics.samples
    assert sample.outcome == "ok"
    assert sample.stream is True
    assert sample.ttft_s == pytest.approx(0.5)
    assert sample.duration_s == pytest.approx(0.8)
    assert (sample.prompt_tokens, sample.completion_tokens) == (11, 3)


async def test_engine_failure_is_recorded_and_still_raised(clock, metrics):
    engine = TimedEngine(clock, error=BackendUnavailable("down"))

    with pytest.raises(BackendUnavailable):
        await measured(engine, metrics, clock).generate(REQUEST)

    assert [s.outcome for s in metrics.samples] == ["error"]


async def test_timeout_is_recorded_as_timeout(clock, metrics):
    engine = TimedEngine(clock, error=GenerationTimeout("slow"))

    with pytest.raises(GenerationTimeout):
        [c async for c in measured(engine, metrics, clock).stream(REQUEST)]

    assert [s.outcome for s in metrics.samples] == ["timeout"]


async def test_unexpected_errors_count_as_errors(clock, metrics):
    with pytest.raises(RuntimeError):
        await measured(TimedEngine(clock, error=RuntimeError("bug")), metrics, clock).generate(
            REQUEST)

    assert [s.outcome for s in metrics.samples] == ["error"]


@pytest.mark.parametrize("error", [Overloaded("busy"), QueueTimeout("waited")])
@pytest.mark.parametrize("stream", [False, True])
async def test_rejected_requests_are_not_recorded_as_served(clock, metrics, error, stream):
    backend = measured(TimedEngine(clock, error=error), metrics, clock)

    with pytest.raises(type(error)):
        if stream:
            [c async for c in backend.stream(REQUEST)]
        else:
            await backend.generate(REQUEST)

    assert metrics.samples == []


async def test_a_client_that_leaves_midway_is_recorded_as_cancelled(clock, metrics):
    backend = measured(TimedEngine(clock, tokens=("a", "b", "c", "d")), metrics, clock)
    stream = backend.stream(REQUEST)

    await anext(stream)  # "a"
    await anext(stream)  # "b"
    await stream.aclose()

    (sample,) = metrics.samples
    assert sample.outcome == "cancelled"
    assert sample.ttft_s == pytest.approx(0.5)
    assert sample.completion_tokens == 2  # no usage yet: one piece counts as one token
    assert sample.prompt_tokens == 0


async def test_without_usage_pieces_stand_in_for_tokens(clock, metrics):
    backend = measured(TimedEngine(clock, report_usage=False), metrics, clock)

    [c async for c in backend.stream(REQUEST)]

    assert metrics.samples[0].completion_tokens == 3


async def test_readiness_comes_from_the_engine(clock, metrics):
    assert await measured(TimedEngine(clock), metrics, clock).is_ready() is True
