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

## Overload protection
The engine can only serve a few requests at once, so the gateway decides who gets in:

| Situation | Answer |
|---|---|
| A slot is free | Served right away |
| All slots busy, queue has room | Waits its turn (up to `QUEUE_TIMEOUT_S`) |
| Queue is full | `429` immediately, with `Retry-After` |
| Waited too long in the queue | `503` with `Retry-After` |
| Answer takes longer than `GENERATION_TIMEOUT_S` | `504` (inside the stream as an error event if it already started) |

Limits are set in `.env` (`MAX_CONCURRENT_REQUESTS`, `MAX_QUEUE_SIZE`, `QUEUE_TIMEOUT_S`, `GENERATION_TIMEOUT_S`). Keep `MAX_CONCURRENT_REQUESTS` equal to the engine's `PARALLEL_SLOTS`. A client that disconnects while waiting or generating gives its place back.

With 2 slots and a queue of 2, a burst of 7 simultaneous requests gives 4 answers (2 served at once, 2 after waiting) and 3 immediate `429`s, and the service keeps working afterwards.

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
