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

## Access control
Set `API_KEYS` in `.env` (`name:key,name:key`) and every request to `/v1/*` must send `Authorization: Bearer <key>`. Generate a key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keys are kept only as hashes in memory, and a wrong key never spends anyone's rate-limit budget.

| Situation | Answer |
|---|---|
| Missing, malformed or wrong key | `401` with `WWW-Authenticate: Bearer` |
| Client exceeded its rate limit | `429` with `Retry-After` (seconds until the next request is allowed) |

The rate limit is a token bucket per client: `RATE_LIMIT_PER_MINUTE` is the sustained rate and `RATE_LIMIT_BURST` how many requests can arrive at once. Set `RATE_LIMIT_PER_MINUTE=0` to turn it off (for example in load tests). With `API_KEYS` empty, authentication is off and everyone shares one anonymous budget; the gateway warns about it on start. The docs page stays public.

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

## Observability
| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness: the gateway process is up |
| `GET /ready` | Readiness: the engine has its model loaded (`503` while it is loading or down) |
| `GET /metrics` | Prometheus metrics |

These three need no API key and should not be exposed to the public internet.

| Metric | What it tells you |
|---|---|
| `tokenscope_time_to_first_token_seconds` | Time until the first token arrives (streamed answers), queue wait included |
| `tokenscope_tokens_per_second{stream}` | Generation speed of finished requests |
| `tokenscope_request_duration_seconds{stream}` | Total time per request, queue wait included |
| `tokenscope_queue_wait_seconds` | Time spent waiting for a free slot |
| `tokenscope_requests_total{outcome,stream}` | Requests served: `ok`, `error`, `timeout`, `cancelled` |
| `tokenscope_rejected_requests_total{reason}` | Turned away: `unauthorized`, `rate_limited`, `overloaded`, `queue_timeout`, `invalid_request` |
| `tokenscope_prompt_tokens_total`, `tokenscope_completion_tokens_total` | Token volume |
| `tokenscope_active_requests`, `tokenscope_queued_requests`, `tokenscope_engine_slots` | Live queue state |
| `tokenscope_system_cpu_percent`, `tokenscope_system_memory_used_bytes`, `tokenscope_gateway_memory_rss_bytes` | Machine and gateway resources |

Streamed speed leaves out the wait for the first token; non-streamed speed is completion tokens over total time. A request cancelled by the client before the engine reports its token count is counted by pieces received (about one token each). The engine itself also exposes its own metrics at `http://localhost:8081/metrics`.

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
