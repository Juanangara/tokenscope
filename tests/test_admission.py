import asyncio
import time

import pytest

from app.application.admission import AdmissionController
from app.application.errors import Overloaded, QueueTimeout


async def hold(controller: AdmissionController, release: asyncio.Event, entered: list | None = None):
    async with controller.slot():
        if entered is not None:
            entered.append(1)
        await release.wait()


async def settle():
    """Let every ready task run until it blocks."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_requests_up_to_the_limit_run_at_once():
    controller = AdmissionController(max_concurrent=2, max_queue=0, queue_timeout_s=1)
    release, entered = asyncio.Event(), []
    tasks = [asyncio.create_task(hold(controller, release, entered)) for _ in range(2)]
    await settle()

    assert len(entered) == 2
    assert controller.running == 2
    assert controller.waiting == 0

    release.set()
    await asyncio.gather(*tasks)
    assert controller.running == 0


async def test_extra_requests_wait_and_run_when_a_slot_frees_up():
    controller = AdmissionController(max_concurrent=1, max_queue=2, queue_timeout_s=1)
    release, entered = asyncio.Event(), []
    tasks = [asyncio.create_task(hold(controller, release, entered)) for _ in range(3)]
    await settle()

    assert len(entered) == 1
    assert (controller.running, controller.waiting) == (1, 2)

    release.set()
    await asyncio.gather(*tasks)
    assert len(entered) == 3
    assert (controller.running, controller.waiting) == (0, 0)


async def test_waiting_requests_are_served_in_arrival_order():
    controller = AdmissionController(max_concurrent=1, max_queue=3, queue_timeout_s=1)
    order = []
    first_release = asyncio.Event()

    async def worker(name: str, release: asyncio.Event | None = None):
        async with controller.slot():
            order.append(name)
            if release:
                await release.wait()

    tasks = [asyncio.create_task(worker("first", first_release))]
    await settle()
    for name in ("second", "third", "fourth"):
        tasks.append(asyncio.create_task(worker(name)))
        await settle()

    first_release.set()
    await asyncio.gather(*tasks)
    assert order == ["first", "second", "third", "fourth"]


async def test_a_full_queue_rejects_immediately():
    controller = AdmissionController(max_concurrent=1, max_queue=1, queue_timeout_s=5)
    release = asyncio.Event()
    running = asyncio.create_task(hold(controller, release))
    queued = asyncio.create_task(hold(controller, release))
    await settle()

    started = time.perf_counter()
    with pytest.raises(Overloaded):
        async with controller.slot():
            pass
    assert time.perf_counter() - started < 0.1  # NFR3: fail fast

    release.set()
    await asyncio.gather(running, queued)


async def test_no_queue_means_busy_slots_reject_right_away():
    controller = AdmissionController(max_concurrent=1, max_queue=0, queue_timeout_s=5)
    release = asyncio.Event()
    running = asyncio.create_task(hold(controller, release))
    await settle()

    with pytest.raises(Overloaded):
        async with controller.slot():
            pass

    release.set()
    await running


async def test_waiting_too_long_raises_queue_timeout():
    controller = AdmissionController(max_concurrent=1, max_queue=1, queue_timeout_s=0.05)
    release = asyncio.Event()
    running = asyncio.create_task(hold(controller, release))
    await settle()

    with pytest.raises(QueueTimeout):
        async with controller.slot():
            pass
    assert controller.waiting == 0

    release.set()
    await running


async def test_a_waiter_that_gives_up_frees_its_queue_place_and_leaks_no_slot():
    controller = AdmissionController(max_concurrent=1, max_queue=1, queue_timeout_s=5)
    release = asyncio.Event()
    running = asyncio.create_task(hold(controller, release))
    waiter = asyncio.create_task(hold(controller, release))
    await settle()
    assert controller.waiting == 1

    waiter.cancel()  # e.g. the client disconnected while queued
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert controller.waiting == 0

    release.set()
    await running
    async with controller.slot():  # the slot is still usable
        assert controller.running == 1


async def test_the_slot_is_released_when_the_work_fails():
    controller = AdmissionController(max_concurrent=1, max_queue=0, queue_timeout_s=1)

    with pytest.raises(RuntimeError):
        async with controller.slot():
            raise RuntimeError("boom")

    assert controller.running == 0
    async with controller.slot():
        pass


@pytest.mark.parametrize("kwargs", [{"max_concurrent": 0, "max_queue": 0},
                                    {"max_concurrent": 1, "max_queue": -1}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        AdmissionController(queue_timeout_s=1, **kwargs)
