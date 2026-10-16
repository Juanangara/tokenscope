from typing import Protocol

from app.domain.models import ChatRequest


class PromptObserver(Protocol):
    def observe(self, request: ChatRequest) -> None:
        """Look at a valid request that is about to be served."""
        ...
