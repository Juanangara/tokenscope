from collections.abc import AsyncIterator
from typing import Protocol

from app.domain.models import ChatChunk, ChatRequest, ChatResult


class BackendUnavailable(Exception):
    """The inference engine could not produce an answer (maps to HTTP 503)."""


class InferenceBackend(Protocol):
    async def generate(self, request: ChatRequest) -> ChatResult: ...

    async def is_ready(self) -> bool:
        """True when the engine has its model loaded and can take requests."""
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Yield the answer piece by piece.

        Closing the iterator (aclose) must stop the generation in the engine.
        """
        ...
