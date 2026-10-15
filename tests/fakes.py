from app.application.ports.inference_backend import BackendUnavailable
from app.domain.models import ChatRequest, ChatResult, Usage


class FakeBackend:
    """In-memory InferenceBackend: no model, instant, and it remembers what it received."""

    def __init__(self, content: str = "hello", fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.requests: list[ChatRequest] = []

    async def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if self.fail:
            raise BackendUnavailable("fake engine is down")
        return ChatResult(self.content, "stop", Usage(prompt_tokens=7, completion_tokens=3))
