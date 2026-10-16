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
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from app.application.drift import DriftSnapshot
from app.application.ports.metrics import RequestSample

# Buckets sized for a CPU-bound engine: answers take seconds, not milliseconds.
DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)
TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
WAIT_BUCKETS = (0.001, 0.01, 0.1, 0.5, 1, 2, 5, 10, 30)
TOKENS_PER_SECOND_BUCKETS = (1, 2, 5, 10, 15, 20, 30, 40, 60, 100, 200)


PROMPT_LENGTH_BUCKETS = (25, 50, 100, 150, 250, 500, 1000, 2000, 4000, 8000)


class _DriftCollector(Collector):
    """Builds the drift metrics at scrape time, so they always reflect the current window.

    The score is only published when it can be trusted (a reference exists and the window has
    enough prompts): a missing sample is honest, a made-up zero would not be.
    """

    def __init__(self, snapshot: Callable[[], DriftSnapshot]) -> None:
        self._snapshot = snapshot

    def collect(self):
        snap = self._snapshot()
        yield GaugeMetricFamily(
            "tokenscope_prompt_drift_ready",
            "1 when drift scores are being published, 0 while the reference or window is filling.",
            value=1.0 if snap.ready else 0.0)
        yield GaugeMetricFamily(
            "tokenscope_prompt_drift_reference_samples",
            "Prompts in the reference distribution (or captured so far).",
            value=snap.reference_samples)
        yield GaugeMetricFamily(
            "tokenscope_prompt_drift_window_samples",
            "Prompts in the window being compared with the reference.", value=snap.window_samples)
        yield GaugeMetricFamily(
            "tokenscope_prompt_drift_threshold",
            "PSI above which a feature counts as drifted.", value=snap.threshold)

        scores = GaugeMetricFamily(
            "tokenscope_prompt_drift_score",
            "Population Stability Index of recent prompts against the reference, per feature.",
            labels=["feature"])
        for feature, score in snap.scores.items():
            scores.add_metric([feature], score)
        yield scores


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
            "Generation speed of finished requests. Time in the queue is never counted; "
            "streamed answers also leave out the wait for the first token.",
            ["stream"], buckets=TOKENS_PER_SECOND_BUCKETS, registry=r)
        self._prompt_tokens = Counter(
            "tokenscope_prompt_tokens_total", "Prompt tokens processed.", registry=r)
        self._completion_tokens = Counter(
            "tokenscope_completion_tokens_total", "Tokens generated.", registry=r)
        self._queue_wait = Histogram(
            "tokenscope_queue_wait_seconds", "Time a request waited for a free slot.",
            buckets=WAIT_BUCKETS, registry=r)
        self._prompt_length = Histogram(
            "tokenscope_prompt_length_chars",
            "Total characters of the messages in each valid request.",
            buckets=PROMPT_LENGTH_BUCKETS, registry=r)
        self._prompts = Counter(
            "tokenscope_prompts_total", "Valid requests by detected language of the prompt.",
            ["language"], registry=r)
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
            # Time spent generating: leave out the queue wait and, for streams, also the wait
            # for the first token (ttft already includes the queue wait).
            lead = sample.ttft_s if sample.ttft_s is not None else sample.queue_wait_s
            window = sample.duration_s - lead
            if window > 0:
                self._tokens_per_second.labels(stream).observe(
                    sample.completion_tokens / window)

    def request_rejected(self, reason: str) -> None:
        self._rejected.labels(reason).inc()

    def queue_waited(self, seconds: float) -> None:
        self._queue_wait.observe(seconds)

    def prompt_observed(self, chars: int, language: str) -> None:
        self._prompt_length.observe(chars)
        self._prompts.labels(language).inc()

    def watch_drift(self, snapshot: Callable[[], DriftSnapshot]) -> None:
        """Publish the drift scores, read fresh at every scrape."""
        self.registry.register(_DriftCollector(snapshot))

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
