from contextlib import asynccontextmanager

import httpx

from app.adapters.inbound.http_api import create_app
from app.adapters.outbound.llama_cpp import LlamaCppBackend
from app.application.ports.inference_backend import InferenceBackend
from app.application.use_cases import GenerateChat
from app.config import Settings
from app.domain.limits import RequestLimits


def build_backend(settings: Settings, client: httpx.AsyncClient) -> InferenceBackend:
    if settings.backend == "llama_cpp":
        return LlamaCppBackend(client)
    raise ValueError(f"unknown backend '{settings.backend}'")


def build_app():
    settings = Settings.from_env()
    client = httpx.AsyncClient(
        base_url=settings.llama_base_url, timeout=settings.backend_timeout_s
    )
    backend = build_backend(settings, client)
    limits = RequestLimits(settings.max_prompt_chars, settings.max_tokens_limit)

    @asynccontextmanager
    async def lifespan(_):
        yield
        await client.aclose()

    return create_app(GenerateChat(backend, limits), settings.model_name, lifespan=lifespan)


app = build_app()
