import asyncio
from contextlib import asynccontextmanager

from app.application.errors import Overloaded, QueueTimeout


class AdmissionController:
    """Decides who gets the engine: a few requests run, a few wait, the rest are turned away.

    - up to `max_concurrent` requests run at the same time;
    - up to `max_queue` more wait their turn, at most `queue_timeout_s` seconds each;
    - anyone else is rejected immediately (Overloaded) instead of piling up.
    """

    def __init__(self, max_concurrent: int, max_queue: int, queue_timeout_s: float) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if max_queue < 0:
            raise ValueError("max_queue must not be negative")
        self._max_queue = max_queue
        self._queue_timeout_s = queue_timeout_s
        self._slots = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
        self.running = 0
        self.waiting = 0

    @asynccontextmanager
    async def slot(self):
        """Hold a slot for the duration of the `async with`; raises if none can be had."""
        if self._slots.locked():  # no free slot, or others already waiting for one
            await self._wait_for_slot()
        else:
            await self._slots.acquire()

        self.running += 1
        try:
            yield
        finally:
            self.running -= 1
            self._slots.release()

    async def _wait_for_slot(self) -> None:
        if self.waiting >= self._max_queue:
            raise Overloaded(
                f"all {self.max_concurrent} slots are busy and the queue "
                f"({self._max_queue}) is full"
            )
        self.waiting += 1
        try:
            async with asyncio.timeout(self._queue_timeout_s):
                await self._slots.acquire()
        except TimeoutError as exc:
            raise QueueTimeout(
                f"waited more than {self._queue_timeout_s:g}s for a free slot"
            ) from exc
        finally:
            self.waiting -= 1
