import json
import math
import mimetypes
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.application.access import AccessControl, Client
from app.application.errors import (
    GenerationTimeout,
    Overloaded,
    QueueTimeout,
    RateLimited,
    Unauthorized,
)
from app.application.ports.inference_backend import BackendUnavailable
from app.application.ports.metrics import MetricsSink, NullMetrics
from app.application.use_cases import GenerateChat, StreamChat
from app.domain.limits import InvalidRequest
from app.domain.models import ChatChunk, ChatMessage, ChatRequest, GenerationParams, Usage


class MessageIn(BaseModel):
    role: str
    content: str


class ChatCompletionIn(BaseModel):
    model: str | None = None
    messages: list[MessageIn]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    stream: bool = False


# What each service failure looks like to the client: (HTTP status, error type).
_FAILURES: dict[type[Exception], tuple[int, str]] = {
    BackendUnavailable: (503, "service_unavailable"),
    Overloaded: (429, "rate_limit_error"),
    QueueTimeout: (503, "service_unavailable"),
    GenerationTimeout: (504, "timeout_error"),
}
RETRY_AFTER_S = "1"

# Failures that turn a request away before it reaches the engine, and how they are counted.
_REJECTIONS: dict[type[Exception], str] = {
    Overloaded: "overloaded",
    QueueTimeout: "queue_timeout",
}


def _error_body(status: int, message: str, error_type: str) -> dict:
    return {"error": {"message": message, "type": error_type, "code": status}}


def _error(status: int, message: str, error_type: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_body(status, message, error_type))


def _to_domain(body: ChatCompletionIn) -> ChatRequest:
    defaults = GenerationParams()
    stop = (body.stop,) if isinstance(body.stop, str) else tuple(body.stop or ())
    params = GenerationParams(
        max_tokens=body.max_tokens,
        temperature=body.temperature if body.temperature is not None else defaults.temperature,
        top_p=body.top_p if body.top_p is not None else defaults.top_p,
        stop=stop,
    )
    messages = tuple(ChatMessage(m.role, m.content) for m in body.messages)
    return ChatRequest(messages=messages, params=params)


def _usage_json(usage: Usage) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _chunk_event(completion_id: str, created: int, model: str, delta: dict,
                 finish_reason: str | None = None, usage: Usage | None = None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = _usage_json(usage)
    return _sse(payload)


async def _event_stream(first: ChatChunk | None, chunks: AsyncIterator[ChatChunk],
                        model: str) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    try:
        yield _chunk_event(completion_id, created, model, {"role": "assistant"})
        pending = [first] if first is not None else []
        while True:
            for chunk in pending:
                delta = {"content": chunk.delta} if chunk.delta else {}
                yield _chunk_event(completion_id, created, model, delta,
                                   chunk.finish_reason, chunk.usage)
            try:
                pending = [await anext(chunks)]
            except StopAsyncIteration:
                break
        yield "data: [DONE]\n\n"
    except (BackendUnavailable, GenerationTimeout) as exc:
        # The 200 status is already sent, so the error travels inside the stream.
        status, error_type = _FAILURES[type(exc)]
        yield _sse(_error_body(status, str(exc), error_type))
    finally:
        # Runs when the stream ends, fails, or the client disconnects (the task is cancelled):
        # closing the iterator closes the connection to the engine, which stops generating.
        await chunks.aclose()


def create_app(generate_chat: GenerateChat, stream_chat: StreamChat, access: AccessControl,
               model_name: str, lifespan=None, *, metrics: MetricsSink | None = None,
               render_metrics: Callable[[], tuple[bytes, str]] | None = None,
               is_ready: Callable[[], Awaitable[bool]] | None = None,
               demo_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="tokenscope", lifespan=lifespan)
    metrics = metrics or NullMetrics()

    if demo_dir is not None and demo_dir.is_dir():
        # Browsers refuse to run a module script served as text/plain, which some Windows
        # setups report for .js: say it explicitly.
        mimetypes.add_type("text/javascript", ".js")
        app.mount("/demo", StaticFiles(directory=demo_dir, html=True), name="demo")

        @app.get("/", include_in_schema=False)
        async def home():
            return RedirectResponse("/demo/")

    async def authorize(authorization: str | None = Header(default=None)) -> Client:
        return access.authorize(authorization)

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(_: Request, exc: Unauthorized):
        metrics.request_rejected("unauthorized")
        response = _error(401, str(exc), "authentication_error")
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(RateLimited)
    async def rate_limited_handler(_: Request, exc: RateLimited):
        metrics.request_rejected("rate_limited")
        response = _error(429, str(exc), "rate_limit_error")
        response.headers["Retry-After"] = str(max(1, math.ceil(exc.retry_after_s)))
        return response

    @app.exception_handler(InvalidRequest)
    async def invalid_request_handler(_: Request, exc: InvalidRequest):
        metrics.request_rejected("invalid_request")
        return _error(400, str(exc), "invalid_request_error")

    @app.exception_handler(RequestValidationError)
    async def malformed_request_handler(_: Request, exc: RequestValidationError):
        metrics.request_rejected("invalid_request")
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return _error(400, f"{where}: {first['msg']}", "invalid_request_error")

    def register_failure(exc_type: type[Exception]) -> None:
        @app.exception_handler(exc_type)
        async def handler(_: Request, exc: Exception):
            if exc_type in _REJECTIONS:
                metrics.request_rejected(_REJECTIONS[exc_type])
            status, error_type = _FAILURES[exc_type]
            headers = {"Retry-After": RETRY_AFTER_S} if status in (429, 503) else None
            response = _error(status, str(exc), error_type)
            if headers:
                response.headers.update(headers)
            return response

    for failure_type in _FAILURES:
        register_failure(failure_type)

    @app.get("/health")
    async def health():
        """Liveness: the gateway process is up."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        """Readiness: the engine has its model loaded and can take requests."""
        if is_ready is None or await is_ready():
            return {"status": "ready"}
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    if render_metrics is not None:
        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint():
            body, content_type = render_metrics()
            return Response(content=body, media_type=content_type)

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionIn, _client: Client = Depends(authorize)):
        request = _to_domain(body)
        model = body.model or model_name

        if body.stream:
            chunks = stream_chat(request)
            # Ask for the first piece before answering: validation errors (400) and an
            # unreachable engine (503) still get a proper status code.
            try:
                first = await anext(chunks)
            except StopAsyncIteration:
                first = None
            return StreamingResponse(
                _event_stream(first, chunks, model),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        result = await generate_chat(request)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": _usage_json(result.usage),
        }

    return app
