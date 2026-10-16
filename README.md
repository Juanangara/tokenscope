# tokenscope

A low-latency LLM inference service with observability, built to run on modest hardware.

It serves a quantized open-source model behind an OpenAI-compatible API, protects itself from overload with a bounded queue, and exposes per-request metrics (time to first token, tokens per second) in Prometheus and Grafana.

> Status: under construction. See [requirements](docs/requirements.md) and [architecture](docs/architecture.md).

## Quick start
Requirements: Docker.

```
cp .env.example .env
sh scripts/download_model.sh      # ~0.8 GB, saved in ./models
docker compose up -d --build
```

One command starts the whole stack. Everything listens on localhost only:

| Service | URL | What it is |
|---|---|---|
| Gateway | http://localhost:8000 | The API (`/docs` for the interactive documentation) |
| Grafana | http://localhost:3000 | Dashboard "tokenscope overview" (read-only without login; admin password in `.env`) |
| Prometheus | http://localhost:9090 | Metrics storage and queries |
| Engine | http://localhost:8081 | llama.cpp server, behind the gateway |

Inside Compose, the gateway lets exactly `PARALLEL_SLOTS` requests run at once, the same number of slots the engine has, so those two cannot get out of sync.

To run the gateway outside Docker instead (for development), keep the engine up and:

```
docker compose up -d llama
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/Mac: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --env-file .env
```

Ask it something:

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

## Benchmark
Load test with [Locust](https://locust.io) against the full stack, on a laptop CPU (Intel i5-1235U, no GPU), comparing three configurations of Llama 3.2 at 1, 2, 4, 8 and 16 concurrent users. Full tables in [benchmarks/REPORT.md](benchmarks/REPORT.md), raw results in `benchmarks/results/`.

![Benchmark comparison](benchmarks/benchmark.png)

What it shows, for the default engine setup (2 slots):

- **Throughput stops growing at 2 users, the number of slots.** The 1B Q4 model goes from 12.9 to 16.2 tokens/s in total when a second user arrives, then stays flat (16.5, 16.0, 15.9 with 4, 8 and 16). Each request gets slower in exchange: 14.0 tokens/s alone, about 8 with two running.
- **Past that point, extra users only wait.** Time to first token (p95) for 1B Q4: 0.27 s with 2 users, 9.4 s with 4, 26.6 s with 8. Latency follows users divided by throughput, so it grows in a straight line while throughput does not.
- **The protection works under 8x overload.** With 16 users, 94-97% of attempts are turned away, each refusal arrives in 43-53 ms (p95, client side), and the requests that were accepted finish with about the same latency as at 8 users (37 s vs 34 s at p95) instead of degrading further. With the heavier 3B model a few waiting requests exceeded the 30 s queue limit and got a `503` (8 at 8 users).
- **Size matters more than quantization.** 3B Q4 gives about 2.4x less throughput than 1B Q4 (6.7-7.0 vs 16.2-16.5 tokens/s with 2 to 4 users). Q8 instead of Q4 on the 1B model costs 12-28% of throughput for a 1.6x bigger file (1.23 vs 0.75 GiB). This benchmark measures speed, not answer quality: whether Q8 or 3B is worth the cost depends on how good the answers need to be.
- **Sizing the queue is a latency decision.** The default queue (8 waiting, up to 30 s) is generous. To keep, for example, time to first token under 10 s with the 1B Q4 model, allow about 4 users in flight (2 running + 2 queued, `MAX_QUEUE_SIZE=2`) and refuse the rest.

Limits of these numbers: one laptop, one run per level of 60 seconds (5 to 22 successful requests per level, so p99 is close to the slowest request and small differences are noise), configurations always run in the same order, and no repetitions to estimate variance. In short, isolated requests earlier in development the 1B Q4 model reached about 30 tokens/s; here it gives about 14 per request under sustained load. I have not isolated the cause (a laptop CPU under sustained load, the Docker network path and 64-token answers all differ from those tests), so treat absolute speeds as indicative and the shape of the curves as the result. Peak memory was not measured reliably (`docker stats` does not count memory-mapped model files); the weights themselves are 0.75 to 1.9 GiB.

To reproduce it:

```
sh scripts/download_benchmark_models.sh     # ~4.1 GB
pip install -r requirements-bench.txt
python benchmarks/run_benchmark.py          # about 30 minutes, restarts the engine for each model
python benchmarks/analyze.py                # writes REPORT.md, benchmark.png and summary.csv
```

The load is closed-loop: each simulated user sends a streamed request, waits for the whole answer and sends the next, honoring `Retry-After` when turned away. Use `127.0.0.1`, not `localhost`, when measuring from Python on Windows: `localhost` resolves to IPv6 first and every new connection then waits about 2 seconds for the refused attempt.

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

Limits are set in `.env` (`MAX_QUEUE_SIZE`, `QUEUE_TIMEOUT_S`, `GENERATION_TIMEOUT_S`, and `MAX_CONCURRENT_REQUESTS`, which Compose derives from `PARALLEL_SLOTS`; set it by hand, equal to `PARALLEL_SLOTS`, only when running the gateway outside Docker). A client that disconnects while waiting or generating gives its place back.

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

Speed never counts time spent in the queue; streamed speed also leaves out the wait for the first token. A request cancelled by the client before the engine reports its token count is counted by pieces received (about one token each). The engine itself also exposes its own metrics at `http://localhost:8081/metrics`.

### Dashboard
Prometheus scrapes the gateway and the engine every 5 seconds, and Grafana ships with the datasource and the "tokenscope overview" dashboard already provisioned (`grafana/`), so there is nothing to configure. It shows what is happening now (active and queued requests, requests/s, p95 time to first token and duration, tokens/s), then time to first token, generation speed, request duration and queue wait over time, outcomes and rejections, token throughput, slots in use, and CPU/RAM.

Latency panels show p50, p95 and p99 as a single blue ramp (the further into the tail, the lighter). CPU and RAM are read from inside Docker, so they describe the virtual machine that runs the containers, not necessarily the whole host. Percentiles come from histogram buckets, so they are estimates between bucket edges.

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
