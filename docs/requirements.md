# Requirements

## Problem

Running a language model in a notebook is easy. Offering it as a service is hard. Once several people use it at the same time, the model alone cannot answer:

- How long until it starts answering, and how fast does it write?
- What happens when more requests arrive than the machine can handle?
- How do I stop one user from taking over the service?
- How do I notice that performance got worse, or that users started asking for different things?
- How much performance does a bigger or less compressed model cost?

**Statement:** build an LLM inference service that runs on modest hardware (CPU), protects itself from overload, and makes its own performance and behavior visible, so an operator can decide with data.

## Actors

| Actor | Needs |
|---|---|
| Client (developer or app) | Ask for chat answers through a known API and receive them as a stream |
| Operator | See service health and performance, and be warned about problems |
| Performance engineer | Compare configurations (model, quantization, concurrency) with numbers |

## Functional requirements

Priority: **M** = must, **S** = should.

### 1. Inference
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR1 | M | `POST /v1/chat/completions` (OpenAI format) returns the answer | A standard OpenAI client works by changing only the base URL |
| FR2 | M | The client can ask for a streamed answer, token by token | The first chunk arrives before generation ends |
| FR3 | S | The client controls `max_tokens`, `temperature`, `top_p`, `stop` | |
| FR4 | M | Invalid or too long input is rejected with a clear error (400) | |

### 2. Overload protection
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR5 | M | Limit how many requests run at once and queue the rest | |
| FR6 | M | When the queue is full, answer 429 instead of hanging | Under overload, rejected requests fail fast and accepted ones finish correctly |
| FR7 | S | If the client disconnects, cancel the generation and free the resource | |
| FR8 | S | API key access and a per-key rate limit | |

### 3. Observability
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR9 | M | Per request: TTFT, tokens per second, total latency, input/output tokens | |
| FR10 | M | System metrics: active and queued requests, CPU and RAM | |
| FR11 | M | Metrics at `/metrics` (Prometheus) and a pre-provisioned Grafana dashboard | |
| FR12 | S | `/health` (alive) and `/ready` (model loaded) | |

### 4. Benchmark
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR13 | M | A load script runs scenarios with growing concurrency | |
| FR14 | M | A report compares p50/p95/p99 latency and tokens/s across configurations: 1B vs 3B, Q4 vs Q8 | |

### 5. Operation
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR15 | M | Everything starts with a single Docker Compose command | |
| FR16 | S | Model and limits are set through environment variables | |
| FR17 | S | The inference engine sits behind an interface (llama.cpp, Ollama, vLLM) | |

### 6. Demonstration
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR18 | M | A small client (chat or simple RAG) uses the service to show it in action | |

### 7. Data drift (last phase, only if time allows)
| ID | Pri | Requirement | Acceptance |
|---|---|---|---|
| FR19 | S | Record prompt statistics (length, language) | |
| FR20 | S | Compare against a reference distribution and raise a metric or alert above a threshold | |

## Non-functional requirements

Targets are **provisional**. They are confirmed or corrected in the benchmark phase, after measuring on the development machine (Intel i5-1235U, 16 GB RAM, no dedicated GPU).

| ID | Category | Requirement |
|---|---|---|
| NFR1 | Reproducibility | A fresh clone runs with `docker compose up` and no paid service or account |
| NFR2 | Performance | Gateway overhead (without the model) under 20 ms at p95 |
| NFR3 | Resilience | Under 2x the sustainable load, the service answers 429 within 100 ms and never crashes |
| NFR4 | Resource use | Total memory of the stack stays under 10 GB with the 3B Q4 model |
| NFR5 | Observability | Every request produces its metrics; none are lost under load |
| NFR6 | Testability | Queue, rate limit and policies are tested without loading a real model (fake backend) |
| NFR7 | Maintainability | Domain and application layers do not import web or HTTP libraries |
| NFR8 | Portability | Works on Windows and Linux; the engine can change without touching the domain |
| NFR9 | Security | No secrets in the repo; API keys come from the environment |

## Out of scope

Model training or fine-tuning, multi-node scaling (Kubernetes), user accounts with OAuth, and an elaborate web UI.
