from typing import Protocol

from app.domain.models import ChatRequest, ChatResult


class BackendUnavailable(Exception):
    """The inference engine could not produce an answer (maps to HTTP 503)."""


class InferenceBackend(Protocol):
    async def generate(self, request: ChatRequest) -> ChatResult: ...
