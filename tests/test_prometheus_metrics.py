import pytest

from app.adapters.outbound.prometheus_metrics import PrometheusMetrics
from app.application.ports.metrics import RequestSample


@pytest.fixture
def metrics():
    return PrometheusMetrics()


def value(metrics: PrometheusMetrics, name: str, **labels) -> float | None:
    return metrics.registry.get_sample_value(name, labels)


def sample(**overrides) -> RequestSample:
    fields = dict(stream=True, outcome="ok", duration_s=2.5, ttft_s=0.5,
                  prompt_tokens=11, completion_tokens=20)
    return RequestSample(**{**fields, **overrides})


def test_requests_are_counted_by_outcome_and_mode(metrics):
    metrics.request_finished(sample())
    metrics.request_finished(sample())
    metrics.request_finished(sample(stream=False, outcome="error"))

    assert value(metrics, "tokenscope_requests_total", outcome="ok", stream="true") == 2
    assert value(metrics, "tokenscope_requests_total", outcome="error", stream="false") == 1


def test_duration_and_first_token_time_are_recorded(metrics):
    metrics.request_finished(sample(duration_s=2.5, ttft_s=0.5))

    assert value(metrics, "tokenscope_request_duration_seconds_sum", stream="true") == 2.5
    assert value(metrics, "tokenscope_request_duration_seconds_count", stream="true") == 1
    assert value(metrics, "tokenscope_time_to_first_token_seconds_sum") == 0.5
    assert value(metrics, "tokenscope_time_to_first_token_seconds_count") == 1


def test_non_streamed_requests_have_no_first_token_time(metrics):
    metrics.request_finished(sample(stream=False, ttft_s=None))

    assert value(metrics, "tokenscope_time_to_first_token_seconds_count") == 0


def test_tokens_are_added_up(metrics):
    metrics.request_finished(sample(prompt_tokens=11, completion_tokens=20))
    metrics.request_finished(sample(prompt_tokens=5, completion_tokens=7))

    assert value(metrics, "tokenscope_prompt_tokens_total") == 16
    assert value(metrics, "tokenscope_completion_tokens_total") == 27


def test_streamed_speed_leaves_out_the_wait_for_the_first_token(metrics):
    # 20 tokens in the 2.0 seconds after the first token arrived: 10 tokens/s
    metrics.request_finished(sample(duration_s=2.5, ttft_s=0.5, completion_tokens=20))

    assert value(metrics, "tokenscope_tokens_per_second_sum", stream="true") == pytest.approx(10)


def test_non_streamed_speed_uses_the_time_spent_generating(metrics):
    metrics.request_finished(sample(stream=False, ttft_s=None, duration_s=4.0,
                                    completion_tokens=20))

    assert value(metrics, "tokenscope_tokens_per_second_sum", stream="false") == pytest.approx(5)


def test_time_in_the_queue_is_never_counted_as_generation_time(metrics):
    # 4s in total, 3s of them waiting for a slot: 20 tokens in 1s of real work
    metrics.request_finished(sample(stream=False, ttft_s=None, duration_s=4.0,
                                    queue_wait_s=3.0, completion_tokens=20))
    # streamed: ttft (2.5s) already contains the 2s wait; 20 tokens in the remaining 1.5s
    metrics.request_finished(sample(stream=True, ttft_s=2.5, duration_s=4.0,
                                    queue_wait_s=2.0, completion_tokens=15))

    assert value(metrics, "tokenscope_tokens_per_second_sum", stream="false") == pytest.approx(20)
    assert value(metrics, "tokenscope_tokens_per_second_sum", stream="true") == pytest.approx(10)


@pytest.mark.parametrize("overrides", [
    {"outcome": "error"}, {"outcome": "timeout"}, {"outcome": "cancelled"},
    {"completion_tokens": 0},
])
def test_speed_is_only_measured_for_answers_that_finished_with_tokens(metrics, overrides):
    metrics.request_finished(sample(**overrides))

    assert value(metrics, "tokenscope_tokens_per_second_count", stream="true") in (None, 0)


def test_rejections_are_counted_by_reason(metrics):
    for reason in ("rate_limited", "rate_limited", "overloaded"):
        metrics.request_rejected(reason)

    assert value(metrics, "tokenscope_rejected_requests_total", reason="rate_limited") == 2
    assert value(metrics, "tokenscope_rejected_requests_total", reason="overloaded") == 1


def test_queue_wait_is_recorded(metrics):
    metrics.queue_waited(0.0)
    metrics.queue_waited(1.5)

    assert value(metrics, "tokenscope_queue_wait_seconds_count") == 2
    assert value(metrics, "tokenscope_queue_wait_seconds_sum") == 1.5


def test_queue_gauges_read_the_live_state_at_every_scrape(metrics):
    state = {"running": 1, "waiting": 0}
    metrics.watch_admission(lambda: state["running"], lambda: state["waiting"], slots=2)

    assert value(metrics, "tokenscope_active_requests") == 1
    assert value(metrics, "tokenscope_engine_slots") == 2

    state.update(running=2, waiting=3)

    assert value(metrics, "tokenscope_active_requests") == 2
    assert value(metrics, "tokenscope_queued_requests") == 3


def test_system_gauges_report_real_values(metrics):
    assert value(metrics, "tokenscope_system_memory_used_bytes") > 0
    assert value(metrics, "tokenscope_gateway_memory_rss_bytes") > 0
    assert 0 <= value(metrics, "tokenscope_system_cpu_percent") <= 100


def test_render_produces_the_prometheus_text_format(metrics):
    metrics.request_finished(sample())

    body, content_type = metrics.render()

    assert content_type.startswith("text/plain")
    text = body.decode()
    assert "# TYPE tokenscope_requests_total counter" in text
    assert 'tokenscope_requests_total{outcome="ok",stream="true"} 1.0' in text


def test_each_instance_has_its_own_registry():
    first, second = PrometheusMetrics(), PrometheusMetrics()
    first.request_rejected("overloaded")

    assert value(second, "tokenscope_rejected_requests_total", reason="overloaded") is None
