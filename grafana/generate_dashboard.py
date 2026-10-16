"""Generates dashboards/tokenscope.json, the Grafana dashboard that Compose provisions.

    python grafana/generate_dashboard.py

The JSON is what Grafana reads, and it is committed; this script exists so the dashboard can be
changed in code (consistent colors, one place for each query) instead of by hand-editing JSON.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "dashboards" / "tokenscope.json"
DS = {"type": "prometheus", "uid": "prometheus"}
R = "$__rate_interval"

# Colors: the validated categorical palette (dark steps, fixed order), a one-hue blue ramp for
# percentiles, and the reserved status colors only for request outcomes.
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"
P_RAMP = {"p50": "#256abf", "p95": "#5598e7", "p99": "#b7d3f6"}  # darker -> lighter = further tail
GOOD, SERIOUS, CRITICAL, MUTED = "#0ca30c", "#ec835a", "#d03b3b", "#898781"

panel_id = 0


def next_id() -> int:
    global panel_id
    panel_id += 1
    return panel_id


def target(expr: str, legend: str, ref: str = "A", instant: bool = False) -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref,
            **({"instant": True} if instant else {})}


def fixed(color: str) -> dict:
    return {"mode": "fixed", "fixedColor": color}


def override(name: str, color: str, extra: list | None = None) -> dict:
    props = [{"id": "color", "value": fixed(color)}] + (extra or [])
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def dashed(name: str) -> dict:
    """A thin dashed line in the muted color: for limits and thresholds, not data."""
    return override(name, MUTED, [
        {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
        {"id": "custom.lineWidth", "value": 1}])


def stat(title: str, expr: str, x: int, unit: str = "short", decimals: int | None = None,
         description: str = "") -> dict:
    defaults = {"unit": unit, "color": {"mode": "fixed", "fixedColor": "text"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": next_id(), "type": "stat", "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": 0, "w": 4, "h": 4},
        "targets": [target(expr, title, instant=True)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "none", "graphMode": "none", "textMode": "value",
                    "justifyMode": "center"},
    }


def series(title: str, targets: list, x: int, y: int, w: int, h: int = 8, unit: str = "short",
           overrides: list | None = None, stacked: bool = False, description: str = "",
           legend: bool = True, soft_min: float | None = 0, decimals: int | None = None) -> dict:
    custom = {
        "drawStyle": "line", "lineWidth": 2, "lineInterpolation": "linear",
        "fillOpacity": 20 if stacked else 0, "gradientMode": "none", "showPoints": "never",
        "spanNulls": False, "axisPlacement": "auto", "axisSoftMin": soft_min,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
        "thresholdsStyle": {"mode": "off"},
    }
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": next_id(), "type": "timeseries", "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {
            "legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def quantiles(metric: str, qs=(0.5, 0.95, 0.99), by: str = "") -> list:
    group = f"sum by (le{', ' + by if by else ''})"
    return [
        target(f"histogram_quantile({q}, {group} (rate({metric}_bucket[{R}])))",
               f"p{int(q * 100)}", ref=chr(ord("A") + i))
        for i, q in enumerate(qs)
    ]


p_overrides = [override(name, color) for name, color in P_RAMP.items()]

panels = [
    # --- Row 0: what is happening right now -------------------------------------------------
    stat("Active requests", "tokenscope_active_requests", 0,
         description="Requests the engine is serving right now."),
    stat("Queued requests", "tokenscope_queued_requests", 4,
         description="Requests waiting for a free slot right now."),
    stat("Requests / s", f"sum(rate(tokenscope_requests_total[{R}]))", 8, "reqps", 2,
         "Requests served per second."),
    stat("p95 time to first token", "histogram_quantile(0.95, sum by (le) "
         f"(rate(tokenscope_time_to_first_token_seconds_bucket[1m])))", 12, "s", 2,
         "Streamed answers, last minute. Includes time waiting in the queue."),
    stat("p95 request duration", "histogram_quantile(0.95, sum by (le) "
         f"(rate(tokenscope_request_duration_seconds_bucket[1m])))", 16, "s", 2,
         "Last minute. Includes time waiting in the queue."),
    stat("Tokens generated / s", f"sum(rate(tokenscope_completion_tokens_total[{R}]))", 20,
         "short", 1, "Total generation throughput of the service."),

    # --- Row 1: how fast does it feel -------------------------------------------------------
    series("Time to first token", quantiles("tokenscope_time_to_first_token_seconds"),
           0, 4, 12, unit="s", overrides=p_overrides,
           description="Streamed answers. Includes time waiting in the queue."),
    series("Generation speed (average per request)", [
        target("sum by (stream) (rate(tokenscope_tokens_per_second_sum[" + R + "])) / "
               "sum by (stream) (rate(tokenscope_tokens_per_second_count[" + R + "]))",
               "stream={{stream}}")],
           12, 4, 12, unit="short", decimals=1,
           overrides=[override("stream=true", BLUE), override("stream=false", ORANGE)],
           description="Tokens per second of finished requests. Streamed answers leave out "
                       "the wait for the first token."),

    # --- Row 2: latency and queue -----------------------------------------------------------
    series("Request duration", quantiles("tokenscope_request_duration_seconds"),
           0, 12, 12, unit="s", overrides=p_overrides,
           description="Total time per request, queue wait included."),
    series("Time waiting in the queue", quantiles("tokenscope_queue_wait_seconds", (0.5, 0.95)),
           12, 12, 12, unit="s", overrides=p_overrides[:2],
           description="Time between asking for a slot and getting it."),

    # --- Row 3: traffic ---------------------------------------------------------------------
    series("Served requests by outcome", [
        target(f"sum by (outcome) (rate(tokenscope_requests_total[{R}]))", "{{outcome}}")],
           0, 20, 8, unit="reqps", stacked=True, decimals=2,
           overrides=[override("ok", GOOD), override("error", CRITICAL),
                      override("timeout", SERIOUS), override("cancelled", MUTED)],
           description="Requests that reached the engine."),
    series("Rejected requests by reason", [
        target(f"sum by (reason) (rate(tokenscope_rejected_requests_total[{R}]))", "{{reason}}")],
           8, 20, 8, unit="reqps", stacked=True, decimals=2,
           overrides=[override("unauthorized", BLUE), override("rate_limited", ORANGE),
                      override("overloaded", AQUA), override("queue_timeout", YELLOW),
                      override("invalid_request", MAGENTA)],
           description="Requests turned away before reaching the engine."),
    series("Token throughput", [
        target(f"sum(rate(tokenscope_completion_tokens_total[{R}]))", "generated", "A"),
        target(f"sum(rate(tokenscope_prompt_tokens_total[{R}]))", "prompt", "B")],
           16, 20, 8, unit="short", decimals=1,
           overrides=[override("generated", BLUE), override("prompt", ORANGE)],
           description="Tokens per second, summed over all requests."),

    # --- Row 4: capacity and resources ------------------------------------------------------
    series("Active and queued requests", [
        target("tokenscope_active_requests", "active", "A"),
        target("tokenscope_queued_requests", "queued", "B"),
        target("tokenscope_engine_slots", "slots", "C")],
           0, 28, 12, unit="short",
           overrides=[override("active", BLUE), override("queued", ORANGE), dashed("slots")],
           description="Slots are the most requests the gateway lets run at once."),
    series("CPU use (whole machine)", [target("tokenscope_system_cpu_percent", "cpu")],
           12, 28, 6, unit="percent", legend=False, decimals=0,
           overrides=[override("cpu", BLUE)],
           description="Seen from inside Docker: the virtual machine that runs the containers."),
    series("RAM in use (whole machine)", [target("tokenscope_system_memory_used_bytes", "ram")],
           18, 28, 6, unit="bytes", legend=False, soft_min=None,
           overrides=[override("ram", BLUE)],
           description="Seen from inside Docker: the virtual machine that runs the containers."),

    # --- Row 5: are the prompts still the ones we tested with? ------------------------------
    series("Prompt drift (PSI against the reference)", [
        target("tokenscope_prompt_drift_score", "{{feature}}", "A"),
        target("tokenscope_prompt_drift_threshold", "threshold", "B")],
           0, 36, 8, unit="short", decimals=2,
           overrides=[override("length", BLUE), override("language", ORANGE),
                      dashed("threshold")],
           description="Under 0.1 stable, 0.1-0.25 some change, over the threshold a significant "
                       "change (the alert fires after two minutes above it). Lines appear once "
                       "the reference (the first 100 prompts) and a window of 100 prompts exist."),
    series("Prompt length", quantiles("tokenscope_prompt_length_chars", (0.5, 0.95)),
           8, 36, 8, unit="short", overrides=p_overrides[:2],
           description="Characters in the messages of each request, p50 and p95."),
    series("Prompts by language", [
        target(f"sum by (language) (rate(tokenscope_prompts_total[{R}]))", "{{language}}")],
           16, 36, 8, unit="reqps", stacked=True, decimals=2,
           overrides=[override("en", BLUE), override("es", ORANGE), override("fr", AQUA),
                      override("de", YELLOW), override("unknown", MUTED)],
           description="Detected language of what users write. 'unknown' is any prompt too "
                       "short (under 40 characters) or too ambiguous to tell."),
]

dashboard = {
    "uid": "tokenscope-overview",
    "title": "tokenscope overview",
    "tags": ["tokenscope", "llm"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 2,
    "editable": False,
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m"]},
    "graphTooltip": 1,
    "panels": panels,
    "templating": {"list": []},
    "annotations": {"list": []},
}

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dashboard, f, indent=2)
    f.write("\n")
print(f"wrote {OUT} with {len(panels)} panels")
