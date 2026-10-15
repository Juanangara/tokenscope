import math


class Unauthorized(Exception):
    """The API key is missing or not valid (maps to HTTP 401)."""


class RateLimited(Exception):
    """This client sent too many requests (maps to HTTP 429)."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(
            f"rate limit exceeded, try again in {max(1, math.ceil(retry_after_s))}s"
        )


class Overloaded(Exception):
    """Every slot is busy and the waiting queue is full (maps to HTTP 429)."""


class QueueTimeout(Exception):
    """The request waited in the queue for too long (maps to HTTP 503)."""


class GenerationTimeout(Exception):
    """The answer took longer than the allowed time (maps to HTTP 504)."""
