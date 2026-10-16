from collections.abc import AsyncIterator

from app.application.drift import observe_safely
from app.application.ports.inference_backend import InferenceBackend
from app.application.ports.prompt_observer import PromptObserver
from app.domain.limits import RequestLimits
from app.domain.models import ChatChunk, ChatRequest, ChatResult


class GenerateChat:
    """Validate a chat request against the service limits and ask the engine for an answer."""

    def __init__(self, backend: InferenceBackend, limits: RequestLimits,
                 observer: PromptObserver | None = None) -> None:
        self._backend = backend
        self._limits = limits
        self._observer = observer

    async def __call__(self, request: ChatRequest) -> ChatResult:
        request = self._limits.with_defaults(request)
        self._limits.validate(request)
        observe_safely(self._observer, request)
        return await self._backend.generate(request)


class StreamChat:
    """Same as GenerateChat, but the answer arrives piece by piece.

    Validation runs when the first piece is requested, before anything is sent to the engine.
    """

    def __init__(self, backend: InferenceBackend, limits: RequestLimits,
                 observer: PromptObserver | None = None) -> None:
        self._backend = backend
        self._limits = limits
        self._observer = observer

    async def __call__(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        request = self._limits.with_defaults(request)
        self._limits.validate(request)
        observe_safely(self._observer, request)
        stream = self._backend.stream(request)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()
