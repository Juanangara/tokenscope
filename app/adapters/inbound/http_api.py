import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.application.ports.inference_backend import BackendUnavailable
from app.application.use_cases import GenerateChat
from app.domain.limits import InvalidRequest
from app.domain.models import ChatMessage, ChatRequest, GenerationParams


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


def _error(status: int, message: str, error_type: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "code": status}},
    )


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


def create_app(generate_chat: GenerateChat, model_name: str, lifespan=None) -> FastAPI:
    app = FastAPI(title="tokenscope", lifespan=lifespan)

    @app.exception_handler(InvalidRequest)
    async def invalid_request_handler(_: Request, exc: InvalidRequest):
        return _error(400, str(exc), "invalid_request_error")

    @app.exception_handler(RequestValidationError)
    async def malformed_request_handler(_: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return _error(400, f"{where}: {first['msg']}", "invalid_request_error")

    @app.exception_handler(BackendUnavailable)
    async def backend_unavailable_handler(_: Request, exc: BackendUnavailable):
        return _error(503, str(exc), "service_unavailable")

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionIn):
        if body.stream:
            raise InvalidRequest("streaming is not supported yet")

        result = await generate_chat(_to_domain(body))
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model or model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            },
        }

    return app
