import pytest
from fastapi.testclient import TestClient

from app.adapters.inbound.http_api import create_app
from app.application.use_cases import GenerateChat
from app.domain.limits import RequestLimits
from tests.fakes import FakeBackend

BODY = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def backend():
    return FakeBackend("hello there")


@pytest.fixture
def client(backend):
    use_case = GenerateChat(backend, RequestLimits(max_prompt_chars=100, max_tokens_limit=50))
    return TestClient(create_app(use_case, model_name="test-model"))


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
        {**BODY, "stream": True},
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
    use_case = GenerateChat(FakeBackend(fail=True), RequestLimits())
    client = TestClient(create_app(use_case, model_name="m"))

    response = client.post("/v1/chat/completions", json=BODY)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"
