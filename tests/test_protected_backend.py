import asyncio
import time

import pytest

from app.application.admission import AdmissionController
from app.application.errors import GenerationTimeout, Overloaded
from app.application.protected_backend import ProtectedBackend
from app.domain.models import ChatMessage, ChatRequest, GenerationParams
from tests.fakes import FakeBackend

REQUEST = ChatRequest((ChatMessage("user", "hi"),), GenerationParams(max_tokens=10))


def protect(backend, max_concurrent=1, max_queue=0, queue_timeout_s=1, generation_timeout_s=5):
    admission = AdmissionController(max_concurrent, max_queue, queue_timeout_s)
    return ProtectedBackend(backend, admission, generation_timeout_s), admission


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_never_runs_more_requests_than_the_limit():
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    protected, _ = protect(backend, max_concurrent=2, max_queue=10)

    tasks = [asyncio.create_task(protected.generate(REQUEST)) for _ in range(6)]
    await settle()
    assert backend.active == 2

    gate.set()
    results = await asyncio.gather(*tasks)
    assert len(results) == 6
    assert backend.max_active == 2


async def test_overload_rejects_the_extra_ones_and_finishes_the_accepted_ones():
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    protected, _ = protect(backend, max_concurrent=1, max_queue=1)

    accepted = [asyncio.create_task(protected.generate(REQUEST)) for _ in range(2)]
    await settle()

    started = time.perf_counter()
    with pytest.raises(Overloaded):
        await protected.generate(REQUEST)
    assert time.perf_counter() - started < 0.1

    gate.set()
    results = await asyncio.gather(*accepted)
    assert [r.content for r in results] == ["hello", "hello"]


async def test_stream_overload_is_raised_on_the_first_piece():
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    protected, _ = protect(backend, max_concurrent=1, max_queue=0)
    running = asyncio.create_task(protected.generate(REQUEST))
    await settle()

    with pytest.raises(Overloaded):
        await anext(protected.stream(REQUEST))

    gate.set()
    await running


async def test_slow_answer_times_out_and_frees_the_slot():
    backend = FakeBackend(delay=1)
    protected, admission = protect(backend, generation_timeout_s=0.05)

    with pytest.raises(GenerationTimeout):
        await protected.generate(REQUEST)

    assert admission.running == 0
    assert backend.active == 0


async def test_stream_that_takes_too_long_times_out_midway():
    backend = FakeBackend(tokens=("a", "b", "c", "d"), delay=0.03)
    protected, admission = protect(backend, generation_timeout_s=0.1)
    received = []

    with pytest.raises(GenerationTimeout):
        async for chunk in protected.stream(REQUEST):
            received.append(chunk.delta)

    assert 0 < len(received) < 4
    assert backend.stream_closed
    assert admission.running == 0


async def test_closing_a_stream_releases_the_slot_and_stops_the_engine():
    backend = FakeBackend(tokens=tuple("abcdef"))
    protected, admission = protect(backend)
    stream = protected.stream(REQUEST)

    await anext(stream)
    assert admission.running == 1
    await stream.aclose()

    assert admission.running == 0
    assert backend.stream_closed


async def test_a_finished_stream_releases_the_slot():
    protected, admission = protect(FakeBackend(tokens=("a", "b")))

    chunks = [c async for c in protected.stream(REQUEST)]

    assert [c.delta for c in chunks] == ["a", "b", ""]
    assert admission.running == 0
