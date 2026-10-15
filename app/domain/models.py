from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GenerationParams:
    max_tokens: int | None = None  # None: the service picks a default (see RequestLimits)
    temperature: float = 0.7
    top_p: float = 1.0
    stop: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    params: GenerationParams = GenerationParams()

    @property
    def prompt_chars(self) -> int:
        return sum(len(m.content) for m in self.messages)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ChatChunk:
    """One piece of a streamed answer. Usage is only set on the last chunk."""

    delta: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    finish_reason: str
    usage: Usage
