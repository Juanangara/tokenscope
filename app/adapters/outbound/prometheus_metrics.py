from collections.abc import Callable

import psutil
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.application.ports.metrics import RequestSample

# Buckets sized for a CPU-bound engine: answers take seconds, not milliseconds.
DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)
TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
WAIT_BUCKETS = (0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10, 30)
TOKENS_PER_SECOND_BUCKETS = (1, 2, 5, 10, 15, 20, 30, 40, 60, 100, 200)


class PrometheusMetrics:
    """MetricsSink that keeps Prometheus metrics in its own registry (not the global one)."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry

        self._requests = Counter(
            "tokenscope_requests_total", "Requests that reached the engine, by outcome.",
            ["outcome", "stream"], registry=r)
        self._duration = Histogram(
            "tokenscope_request_duration_seconds",
            "Total time of a request, including time waiting in the queue.",
            ["stream"], buckets=DURATION_BUCKETS, registry=r)
        self._ttft = Histogram(
            "tokenscope_time_to_first_token_seconds",
            "Time from the request to the first generated token (streamed answers).",
            buckets=TTFT_BUCKETS, registry=r)
        self._tokens_per_second = Histogram(
            "tokenscope_tokens_per_second",
            "Generation speed of finished requests. Streamed answers exclude the wait for the "
            "first token; non-streamed ones are completion tokens over total time.",
            ["stream"], buckets=TOKENS_PER_SECOND_BUCKETS, registry=r)
        self._prompt_tokens = Counter(
            "tokenscope_prompt_tokens_total", "Prompt tokens processed.", registry=r)
        self._completion_tokens = Counter(
            "tokenscope_completion_tokens_total", "Tokens generated.", registry=r)
        self._queue_wait = Histogram(
            "tokenscope_queue_wait_seconds", "Time a request waited for a free slot.",
            buckets=WAIT_BUCKETS, registry=r)
        self._rejected = Counter(
            "tokenscope_rejected_requests_total",
            "Requests turned away before reaching the engine, by reason.",
            ["reason"], registry=r)

        self._active = Gauge(
            "tokenscope_active_requests", "Requests being served by the engine now.",
            registry=r)
        self._queued = Gauge(
            "tokenscope_queued_requests", "Requests waiting for a free slot now.", registry=r)
        self._slots = Gauge(
            "tokenscope_engine_slots", "Requests the gateway lets run at the same time.",
            registry=r)
        self._register_system_gauges()

    def request_finished(self, sample: RequestSample) -> None:
        stream = str(sample.stream).lower()
        self._requests.labels(sample.outcome, stream).inc()
        self._duration.labels(stream).observe(sample.duration_s)
        self._prompt_tokens.inc(sample.prompt_tokens)
        self._completion_tokens.inc(sample.completion_tokens)

        if sample.ttft_s is not None:
            self._ttft.observe(sample.ttft_s)

        if sample.outcome == "ok" and sample.completion_tokens > 0:
            window = sample.duration_s - (sample.ttft_s or 0.0)
            if window > 0:
                self._tokens_per_second.labels(stream).observe(
                    sample.completion_tokens / window)

    def request_rejected(self, reason: str) -> None:
        self._rejected.labels(reason).inc()

    def queue_waited(self, seconds: float) -> None:
        self._queue_wait.observe(seconds)

    def watch_admission(self, running: Callable[[], float], waiting: Callable[[], float],
                        slots: float) -> None:
        """Read the live queue state at every scrape."""
        self._active.set_function(running)
        self._queued.set_function(waiting)
        self._slots.set(slots)

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST

    def _register_system_gauges(self) -> None:
        # psutil works on Windows too, unlike the default process collector (Linux only).
        psutil.cpu_percent(None)  # the first reading is meaningless; later ones cover the
        process = psutil.Process()  # time since the previous scrape

        Gauge("tokenscope_system_cpu_percent", "CPU use of the whole machine since the last scrape.",
              registry=self.registry).set_function(lambda: psutil.cpu_percent(None))
        Gauge("tokenscope_system_memory_used_bytes", "RAM in use on the whole machine.",
              registry=self.registry).set_function(lambda: psutil.virtual_memory().used)
        Gauge("tokenscope_gateway_memory_rss_bytes", "RAM used by the gateway process.",
              registry=self.registry).set_function(lambda: process.memory_info().rss)
