import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from app.adapters.inbound.http_api import create_app
from app.adapters.outbound.llama_cpp import LlamaCppBackend
from app.adapters.outbound.prometheus_metrics import PrometheusMetrics
from app.adapters.outbound.token_bucket import TokenBucketRateLimiter, UnlimitedRateLimiter
from app.application.access import AccessControl
from app.application.admission import AdmissionController
from app.application.measured_backend import MeasuredBackend
from app.application.ports.inference_backend import InferenceBackend
from app.application.ports.rate_limiter import RateLimiter
from app.application.protected_backend import ProtectedBackend
from app.application.use_cases import GenerateChat, StreamChat
from app.config import Settings
from app.domain.limits import RequestLimits

logger = logging.getLogger("tokenscope")

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


def build_backend(settings: Settings, client: httpx.AsyncClient) -> InferenceBackend:
    if settings.backend == "llama_cpp":
        return LlamaCppBackend(client)
    raise ValueError(f"unknown backend '{settings.backend}'")


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_per_minute <= 0:
        return UnlimitedRateLimiter()
    return TokenBucketRateLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)


def build_app():
    settings = Settings.from_env()
    client = httpx.AsyncClient(
        base_url=settings.llama_base_url, timeout=settings.backend_timeout_s
    )
    metrics = PrometheusMetrics()
    admission = AdmissionController(
        settings.max_concurrent_requests, settings.max_queue_size, settings.queue_timeout_s,
        metrics,
    )
    metrics.watch_admission(
        running=lambda: admission.running,
        waiting=lambda: admission.waiting,
        slots=settings.max_concurrent_requests,
    )
    # Order matters: measuring outside the protection means durations include queue time.
    backend = MeasuredBackend(
        ProtectedBackend(build_backend(settings, client), admission,
                         settings.generation_timeout_s),
        metrics,
    )
    limits = RequestLimits(settings.max_prompt_chars, settings.max_tokens_limit)
    access = AccessControl(settings.api_keys, build_rate_limiter(settings))

    if not access.auth_enabled:
        logger.warning("API_KEYS is empty: authentication is OFF, anyone can use this service")

    @asynccontextmanager
    async def lifespan(_):
        yield
        await client.aclose()

    return create_app(
        GenerateChat(backend, limits),
        StreamChat(backend, limits),
        access,
        settings.model_name,
        lifespan=lifespan,
        metrics=metrics,
        render_metrics=metrics.render,
        is_ready=backend.is_ready,
        demo_dir=DEMO_DIR,
    )


app = build_app()
