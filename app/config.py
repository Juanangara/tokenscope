import os
from dataclasses import dataclass, field


def parse_api_keys(raw: str) -> dict[str, str]:
    """Read 'alice:key1,bob:key2' (or just 'key1,key2') into {client name: key}."""
    keys: dict[str, str] = {}
    for position, entry in enumerate(filter(None, (e.strip() for e in raw.split(","))), 1):
        name, separator, key = entry.partition(":")
        if not separator:
            name, key = f"client-{position}", entry
        name, key = name.strip(), key.strip()
        if not name or not key:
            raise ValueError(f"invalid API_KEYS entry at position {position}")
        if name in keys:
            raise ValueError(f"duplicated client name '{name}' in API_KEYS")
        if key in keys.values():
            raise ValueError(f"duplicated key for client '{name}' in API_KEYS")
        keys[name] = key
    return keys


@dataclass(frozen=True)
class Settings:
    backend: str = "llama_cpp"
    llama_base_url: str = "http://localhost:8081"
    backend_timeout_s: float = 120.0
    model_name: str = "llama-3.2-1b-instruct"
    max_prompt_chars: int = 8000
    max_tokens_limit: int = 1024
    max_concurrent_requests: int = 2
    max_queue_size: int = 8
    queue_timeout_s: float = 30.0
    generation_timeout_s: float = 120.0
    api_keys: dict[str, str] = field(default_factory=dict)
    rate_limit_per_minute: float = 120.0  # 0 turns rate limiting off
    rate_limit_burst: int = 20

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            backend=os.getenv("BACKEND", defaults.backend),
            llama_base_url=os.getenv("LLAMA_BASE_URL", defaults.llama_base_url),
            backend_timeout_s=float(os.getenv("BACKEND_TIMEOUT_S", defaults.backend_timeout_s)),
            model_name=os.getenv("MODEL_NAME", defaults.model_name),
            max_prompt_chars=int(os.getenv("MAX_PROMPT_CHARS", defaults.max_prompt_chars)),
            max_tokens_limit=int(os.getenv("MAX_TOKENS_LIMIT", defaults.max_tokens_limit)),
            max_concurrent_requests=int(
                os.getenv("MAX_CONCURRENT_REQUESTS", defaults.max_concurrent_requests)
            ),
            max_queue_size=int(os.getenv("MAX_QUEUE_SIZE", defaults.max_queue_size)),
            queue_timeout_s=float(os.getenv("QUEUE_TIMEOUT_S", defaults.queue_timeout_s)),
            generation_timeout_s=float(
                os.getenv("GENERATION_TIMEOUT_S", defaults.generation_timeout_s)
            ),
            api_keys=parse_api_keys(os.getenv("API_KEYS", "")),
            rate_limit_per_minute=float(
                os.getenv("RATE_LIMIT_PER_MINUTE", defaults.rate_limit_per_minute)
            ),
            rate_limit_burst=int(os.getenv("RATE_LIMIT_BURST", defaults.rate_limit_burst)),
        )
