import hashlib
from dataclasses import dataclass

from app.application.errors import RateLimited, Unauthorized
from app.application.ports.rate_limiter import RateLimiter

ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class Client:
    id: str


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


class AccessControl:
    """Who is calling, and are they within their request budget?

    `api_keys` maps a client name to its key. With no keys configured, authentication is off
    and every caller is the same anonymous client (handy for local development).
    Keys are only kept as hashes, and a wrong key never spends rate-limit budget.
    """

    def __init__(self, api_keys: dict[str, str], rate_limiter: RateLimiter) -> None:
        self._clients = {_digest(key): name for name, key in api_keys.items()}
        self._rate_limiter = rate_limiter

    @property
    def auth_enabled(self) -> bool:
        return bool(self._clients)

    def authorize(self, authorization: str | None) -> Client:
        client = self._authenticate(authorization)
        decision = self._rate_limiter.check(client.id)
        if not decision.allowed:
            raise RateLimited(decision.retry_after_s)
        return client

    def _authenticate(self, authorization: str | None) -> Client:
        if not self.auth_enabled:
            return Client(ANONYMOUS)

        if not authorization:
            raise Unauthorized("missing API key, send 'Authorization: Bearer <key>'")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("malformed Authorization header, expected 'Bearer <key>'")

        name = self._clients.get(_digest(token.strip()))
        if name is None:
            raise Unauthorized("invalid API key")
        return Client(name)
