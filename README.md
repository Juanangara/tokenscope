# tokenscope

A low-latency LLM inference service with observability, built to run on modest hardware.

It serves a quantized open-source model behind an OpenAI-compatible API, protects itself from overload with a bounded queue, and exposes per-request metrics (time to first token, tokens per second) in Prometheus and Grafana.

> Status: in design. See [requirements](docs/requirements.md) and [architecture](docs/architecture.md).

## Quick start
Requirements: Docker.

```
cp .env.example .env
sh scripts/download_model.sh      # ~0.8 GB, saved in ./models
docker compose up -d
```

Run the gateway:

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/Mac: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --env-file .env
```

Ask it something (the engine itself is on port 8081, the gateway on 8000):

```
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say hello."}],"max_tokens":40}'
```

Add `"stream": true` to receive the answer token by token as server-sent events (OpenAI format, ending with `data: [DONE]`). The last chunk before `[DONE]` carries the token usage. If the client disconnects, the generation is stopped in the engine so the slot is freed right away.

```
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Count to 5."}],"stream":true}'
```

Interactive docs at http://localhost:8000/docs. Errors follow the OpenAI shape: `400` for invalid input, `503` when the engine is unreachable.

Tests (no model needed, they use a fake backend):

```
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

On the development machine (i5-1235U, no GPU) the 1B Q4 model generates around 30 tokens/s.

## Planned phases
1. Docker Compose with a llama.cpp server answering with a small model
2. Gateway (FastAPI) with `/v1/chat/completions` and an `InferenceBackend` port
3. Streaming (SSE) and cancellation on client disconnect
4. Bounded queue, concurrency limit, 429 and timeouts
5. API keys and rate limiting
6. Metrics (TTFT, tokens/s, queue), `/metrics`, `/health`, `/ready`
7. Prometheus and Grafana with a provisioned dashboard
8. Load tests and comparison report (1B vs 3B, Q4 vs Q8)
9. Demo client and final README
10. Data drift detection (only if time allows)
