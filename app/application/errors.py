class Overloaded(Exception):
    """Every slot is busy and the waiting queue is full (maps to HTTP 429)."""


class QueueTimeout(Exception):
    """The request waited in the queue for too long (maps to HTTP 503)."""


class GenerationTimeout(Exception):
    """The answer took longer than the allowed time (maps to HTTP 504)."""
