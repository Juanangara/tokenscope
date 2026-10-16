import json

import pytest

pytest.importorskip("matplotlib")  # benchmark-only dependency (requirements-bench.txt)

from benchmarks.analyze import load_level, percentile  # noqa: E402


def row(start=0.0, status=200, error=None, total_s=10.0, ttft_s=2.0, tokens=64):
    return {"start": start, "status": status, "error": error, "prompt_chars": 50,
            "total_s": total_s, "ttft_s": ttft_s, "prompt_tokens": 40,
            "completion_tokens": tokens}


def level(tmp_path, rows):
    path = tmp_path / "x_c1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return load_level(path)


def test_percentile_of_nothing_is_none():
    assert percentile([], 95) is None


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([0, 10], 95) == pytest.approx(9.5)


def test_requests_are_sorted_into_ok_rejected_timeout_and_other(tmp_path):
    m = level(tmp_path, [
        row(), row(), row(status=429, ttft_s=None, tokens=None), row(status=503, ttft_s=None,
                                                                        tokens=None),
        row(status=504, ttft_s=None, tokens=None),
        row(error="timeout_error"),
        row(status=500, ttft_s=None, tokens=None),
    ])

    assert m["attempts"] == 7
    assert m["ok"] == 2
    assert m["rejected"] == 2          # 429 and 503: the service protecting itself
    assert m["timeouts"] == 2          # a 504 and a stream that ended in a timeout error
    assert m["other_errors"] == 1
    assert m["rejected_pct"] == pytest.approx(100 * 2 / 7)


def test_refusals_are_split_by_status(tmp_path):
    m = level(tmp_path, [row(), row(status=429, ttft_s=None, tokens=None),
                         row(status=429, ttft_s=None, tokens=None),
                         row(status=503, ttft_s=None, tokens=None)])

    assert (m["rejected_429"], m["rejected_503"]) == (2, 1)


def test_time_to_be_refused_is_measured_in_milliseconds_for_429s_only(tmp_path):
    m = level(tmp_path, [row(), row(status=429, total_s=0.020, ttft_s=None, tokens=None),
                         row(status=429, total_s=0.040, ttft_s=None, tokens=None),
                         row(status=503, total_s=30.0, ttft_s=None, tokens=None)])

    assert m["refused_p95_ms"] == pytest.approx(39, abs=1)  # the 503 that waited 30 s is excluded


def test_without_429s_there_is_no_refusal_time(tmp_path):
    assert level(tmp_path, [row()])["refused_p95_ms"] is None


def test_the_refusal_summary_names_each_kind():
    from benchmarks.analyze import turned_away

    base = {"rejected": 0, "rejected_429": 0, "rejected_503": 0, "rejected_pct": 0.0}

    assert turned_away(base) == "0"
    assert turned_away({**base, "rejected": 3, "rejected_429": 3, "rejected_pct": 30}) == \
        "3 x 429 (30%)"
    assert turned_away({**base, "rejected": 5, "rejected_429": 2, "rejected_503": 3,
                        "rejected_pct": 50}) == "2 x 429 + 3 x 503 (50%)"


def test_speed_counts_tokens_after_the_first_over_the_time_they_took(tmp_path):
    # 64 tokens, first after 2s, finished at 10s: 63 more tokens in 8s
    m = level(tmp_path, [row(total_s=10.0, ttft_s=2.0, tokens=64)])

    assert m["speed_p50"] == pytest.approx(63 / 8)


def test_aggregate_throughput_uses_the_whole_window(tmp_path):
    # two requests of 64 tokens: one from t=0 to t=10, the other from t=5 to t=20 -> 128 / 20
    m = level(tmp_path, [row(start=0, total_s=10), row(start=5, total_s=15)])

    assert m["tokens_per_s"] == pytest.approx(128 / 20)
    assert m["req_per_s"] == pytest.approx(2 / 20)


def test_latency_and_ttft_percentiles_ignore_failed_requests(tmp_path):
    m = level(tmp_path, [row(total_s=10, ttft_s=1), row(total_s=20, ttft_s=3),
                         row(status=429, total_s=0.01, ttft_s=None, tokens=None)])

    assert m["lat_p50"] == pytest.approx(15)
    assert m["ttft_p50"] == pytest.approx(2)
