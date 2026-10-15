import asyncio
from collections.abc import AsyncIterator

from app.application.admission import AdmissionController
from app.application.errors import GenerationTimeout
from app.application.ports.inference_backend import InferenceBackend
from app.domain.models import ChatChunk, ChatRequest, ChatResult


class ProtectedBackend:
    """Decorator over an InferenceBackend: admission control plus a time limit per request.

    The slot is held while the answer is produced and released when it ends, fails, times out
    or the consumer stops listening.
    """

    def __init__(self, inner: InferenceBackend, admission: AdmissionController,
                 generation_timeout_s: float) -> None:
        self._inner = inner
        self._admission = admission
        self._timeout_s = generation_timeout_s

    async def is_ready(self) -> bool:
        return await self._inner.is_ready()

    async def generate(self, request: ChatRequest) -> ChatResult:
        async with self._admission.slot():
            try:
                async with asyncio.timeout(self._timeout_s):
                    return await self._inner.generate(request)
            except TimeoutError as exc:
                raise self._timeout() from exc

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        async with self._admission.slot():
            deadline = asyncio.get_running_loop().time() + self._timeout_s
            chunks = self._inner.stream(request)
            try:
                while True:
                    try:
                        async with asyncio.timeout_at(deadline):
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        return
                    except TimeoutError as exc:
                        raise self._timeout() from exc
                    yield chunk
            finally:
                await chunks.aclose()

    def _timeout(self) -> GenerationTimeout:
        return GenerationTimeout(f"the answer took longer than {self._timeout_s:g}s")
