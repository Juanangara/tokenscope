import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.inbound.http_api import create_app
from app.adapters.outbound.token_bucket import TokenBucketRateLimiter, UnlimitedRateLimiter
from app.application.access import AccessControl
from app.application.errors import GenerationTimeout, Overloaded, QueueTimeout
from app.application.use_cases import GenerateChat, StreamChat
from app.domain.limits import RequestLimits
from app.domain.models import ChatChunk
from tests.fakes import FakeBackend

BODY = {"messages": [{"role": "user", "content": "hi"}]}
LIMITS = RequestLimits(max_prompt_chars=100, max_tokens_limit=50)


def make_app(backend, limits=LIMITS, access=None):
    access = access or AccessControl({}, UnlimitedRateLimiter())
    return create_app(GenerateChat(backend, limits), StreamChat(backend, limits), access,
                      model_name="test-model")


def sse_events(text: str) -> list[str]:
    return [block[len("data: "):] for block in text.strip().split("\n\n")]


@pytest.fixture
def backend():
    return FakeBackend("hello there")


@pytest.fixture
def client(backend):
    return TestClient(make_app(backend))


def test_returns_an_openai_style_completion(client):
    response = client.post("/v1/chat/completions", json=BODY)

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "test-model"
    assert data["choices"][0]["message"] == {"role": "assistant", "content": "hello there"}
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def test_generation_parameters_reach_the_backend(client, backend):
    client.post(
        "/v1/chat/completions",
        json={**BODY, "max_tokens": 20, "temperature": 0.1, "top_p": 0.5, "stop": "END"},
    )

    params = backend.requests[0].params
    assert (params.max_tokens, params.temperature, params.top_p) == (20, 0.1, 0.5)
    assert params.stop == ("END",)


def test_stop_accepts_a_list(client, backend):
    client.post("/v1/chat/completions", json={**BODY, "stop": ["a", "b"]})

    assert backend.requests[0].params.stop == ("a", "b")


def test_request_model_name_is_echoed(client):
    response = client.post("/v1/chat/completions", json={**BODY, "model": "other"})

    assert response.json()["model"] == "other"


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "robot", "content": "hi"}]},
        {"messages": [{"role": "user", "content": "x" * 101}]},
        {**BODY, "max_tokens": 51},
        {**BODY, "temperature": 5},
        {**BODY, "stream": True, "max_tokens": 51},
        {"messages": [{"role": "robot", "content": "hi"}], "stream": True},
    ],
)
def test_rule_violations_return_400(client, body):
    response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_malformed_body_returns_400(client):
    response = client.post("/v1/chat/completions", json={"nothing": 1})

    assert response.status_code == 400
    assert "messages" in response.json()["error"]["message"]


def test_backend_failure_returns_503():
    client = TestClient(make_app(FakeBackend(fail=True)))

    response = client.post("/v1/chat/completions", json=BODY)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"


@pytest.mark.parametrize(
    "error, status, error_type",
    [
        (Overloaded("busy"), 429, "rate_limit_error"),
        (QueueTimeout("waited"), 503, "service_unavailable"),
        (GenerationTimeout("slow"), 504, "timeout_error"),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_protection_errors_get_their_own_status(error, status, error_type, stream):
    client = TestClient(make_app(FakeBackend(error=error)))

    response = client.post("/v1/chat/completions", json={**BODY, "stream": stream})

    assert response.status_code == status
    assert response.json()["error"]["type"] == error_type
    if status in (429, 503):
        assert response.headers["retry-after"] == "1"


class TestAccess:
    AUTH = {"Authorization": "Bearer secret"}

    def secured_client(self, backend, limiter=None):
        access = AccessControl({"alice": "secret"}, limiter or UnlimitedRateLimiter())
        return TestClient(make_app(backend, access=access))

    @pytest.mark.parametrize("stream", [False, True])
    def test_requests_without_a_key_get_401(self, backend, stream):
        client = self.secured_client(backend)

        response = client.post("/v1/chat/completions", json={**BODY, "stream": stream})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["type"] == "authentication_error"
        assert backend.requests == []

    def test_a_wrong_key_gets_401(self, backend):
        client = self.secured_client(backend)

        response = client.post("/v1/chat/completions", json=BODY,
                               headers={"Authorization": "Bearer nope"})

        assert response.status_code == 401
        assert "invalid" in response.json()["error"]["message"]

    @pytest.mark.parametrize("stream", [False, True])
    def test_a_valid_key_gets_through(self, backend, stream):
        client = self.secured_client(backend)

        response = client.post("/v1/chat/completions", json={**BODY, "stream": stream},
                               headers=self.AUTH)

        assert response.status_code == 200

    def test_exceeding_the_rate_limit_gets_429_with_retry_after(self, backend):
        limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=2)
        client = self.secured_client(backend, limiter)

        statuses = [client.post("/v1/chat/completions", json=BODY, headers=self.AUTH).status_code
                    for _ in range(3)]
        limited = client.post("/v1/chat/completions", json=BODY, headers=self.AUTH)

        assert statuses == [200, 200, 429]
        assert limited.status_code == 429
        assert limited.json()["error"]["type"] == "rate_limit_error"
        assert int(limited.headers["retry-after"]) >= 1
        assert len(backend.requests) == 2  # refused requests never reach the engine

    def test_documentation_stays_public(self, backend):
        client = self.secured_client(backend)

        assert client.get("/docs").status_code == 200


def test_midstream_generation_timeout_is_reported_inside_the_stream():
    class TimesOutAfterFirstPiece(FakeBackend):
        async def stream(self, request):
            yield ChatChunk(delta="a")
            raise GenerationTimeout("slow")

    client = TestClient(make_app(TimesOutAfterFirstPiece()))

    response = client.post("/v1/chat/completions", json={**BODY, "stream": True})

    assert response.status_code == 200
    events = sse_events(response.text)
    assert "[DONE]" not in events
    error = json.loads(events[-1])["error"]
    assert (error["code"], error["type"]) == (504, "timeout_error")


def test_stream_returns_server_sent_events(client):
    response = client.post("/v1/chat/completions", json={**BODY, "stream": True})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    assert events[-1] == "[DONE]"

    chunks = [json.loads(e) for e in events[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}

    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["completion_tokens"] == 2


def test_stream_with_engine_down_returns_503_status():
    client = TestClient(make_app(FakeBackend(fail=True)))

    response = client.post("/v1/chat/completions", json={**BODY, "stream": True})

    assert response.status_code == 503


def test_stream_that_breaks_midway_ends_with_an_error_event():
    client = TestClient(make_app(FakeBackend(tokens=("a", "b", "c"), fail_after=2)))

    response = client.post("/v1/chat/completions", json={**BODY, "stream": True})

    assert response.status_code == 200  # headers were already sent
    events = sse_events(response.text)
    assert "[DONE]" not in events
    assert json.loads(events[-1])["error"]["type"] == "service_unavailable"


async def test_client_disconnect_stops_the_generation():
    """Simulate a client that goes away after receiving the first piece of the answer."""
    backend = FakeBackend(tokens=tuple("x" * 1000))
    app = make_app(backend)
    body = json.dumps({**BODY, "stream": True}).encode()

    disconnected = asyncio.Event()
    request_sent = False
    body_messages = 0

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal body_messages
        if message["type"] == "http.response.body" and message.get("body"):
            body_messages += 1
            if body_messages == 2:  # the role chunk and one token have been delivered
                disconnected.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions", "query_string": b"", "scheme": "http",
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
        "client": ("test", 1), "server": ("test", 80),
    }

    await asyncio.wait_for(app(scope, receive, send), timeout=5)

    assert backend.stream_closed
    assert body_messages < 100  # nowhere near the 1000 tokens the engine could have produced
