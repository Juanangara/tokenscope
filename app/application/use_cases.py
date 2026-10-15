from app.application.ports.inference_backend import InferenceBackend
from app.domain.limits import RequestLimits
from app.domain.models import ChatRequest, ChatResult


class GenerateChat:
    """Validate a chat request against the service limits and ask the engine for an answer."""

    def __init__(self, backend: InferenceBackend, limits: RequestLimits) -> None:
        self._backend = backend
        self._limits = limits

    async def __call__(self, request: ChatRequest) -> ChatResult:
        request = self._limits.with_defaults(request)
        self._limits.validate(request)
        return await self._backend.generate(request)
