from dataclasses import dataclass, replace

from app.domain.models import ChatRequest

VALID_ROLES = ("system", "user", "assistant")
DEFAULT_MAX_TOKENS = 256
MAX_STOP_SEQUENCES = 4


class InvalidRequest(ValueError):
    """The request breaks a rule of the service (maps to HTTP 400)."""


@dataclass(frozen=True)
class RequestLimits:
    max_prompt_chars: int = 8000
    max_tokens_limit: int = 1024

    def with_defaults(self, request: ChatRequest) -> ChatRequest:
        """Fill in max_tokens when the client left it out, never above the service limit."""
        if request.params.max_tokens is not None:
            return request
        max_tokens = min(DEFAULT_MAX_TOKENS, self.max_tokens_limit)
        return replace(request, params=replace(request.params, max_tokens=max_tokens))

    def validate(self, request: ChatRequest) -> None:
        if not request.messages:
            raise InvalidRequest("messages must not be empty")

        for message in request.messages:
            if message.role not in VALID_ROLES:
                raise InvalidRequest(
                    f"invalid role '{message.role}', expected one of {', '.join(VALID_ROLES)}"
                )

        if request.prompt_chars == 0:
            raise InvalidRequest("messages must contain some text")
        if request.prompt_chars > self.max_prompt_chars:
            raise InvalidRequest(
                f"prompt is too long: {request.prompt_chars} characters, "
                f"the limit is {self.max_prompt_chars}"
            )

        params = request.params
        if params.max_tokens is not None and not 1 <= params.max_tokens <= self.max_tokens_limit:
            raise InvalidRequest(f"max_tokens must be between 1 and {self.max_tokens_limit}")
        if not 0.0 <= params.temperature <= 2.0:
            raise InvalidRequest("temperature must be between 0 and 2")
        if not 0.0 < params.top_p <= 1.0:
            raise InvalidRequest("top_p must be greater than 0 and at most 1")
        if len(params.stop) > MAX_STOP_SEQUENCES:
            raise InvalidRequest(f"at most {MAX_STOP_SEQUENCES} stop sequences are allowed")
