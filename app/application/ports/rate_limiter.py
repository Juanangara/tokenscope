from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_s: float = 0.0


class RateLimiter(Protocol):
    def check(self, client_id: str) -> RateLimitDecision:
        """Spend one request from the client's budget, or say how long to wait."""
        ...
