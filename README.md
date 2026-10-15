# tokenscope

A low-latency LLM inference service with observability, built to run on modest hardware.

It serves a quantized open-source model behind an OpenAI-compatible API, protects itself from overload with a bounded queue, and exposes per-request metrics (time to first token, tokens per second) in Prometheus and Grafana.

> Status: in design. See [requirements](docs/requirements.md) and [architecture](docs/architecture.md).

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
