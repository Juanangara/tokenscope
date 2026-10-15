import os
from dataclasses import dataclass


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
        )
