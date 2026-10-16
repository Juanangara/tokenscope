"""Turns the raw load-test results into REPORT.md, summary.csv and a chart.

    python benchmarks/analyze.py

Reads benchmarks/results/<config>_c<users>.jsonl (one line per request, written by
locustfile.py) and <config>_memory.json (written by run_benchmark.py).
"""
import csv
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

MACHINE = ("Intel Core i5-1235U (10 cores, 12 threads), 16 GB RAM, no dedicated GPU. "
           "Docker Desktop with 12 CPUs and 8 GB; engine with 2 parallel slots, 6 threads, "
           "4096-token context.")

# Fixed order and color per configuration (color follows the entity, never its rank).
CONFIGS = {
    "1b-q4": ("1B Q4_K_M", "#2a78d6"),
    "1b-q8": ("1B Q8_0", "#eb6834"),
    "3b-q4": ("3B Q4_K_M", "#1baf7a"),
}
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"

REJECTED = {429, 503}  # the service protecting itself


def percentile(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def ms(seconds: float | None) -> float | None:
    return None if seconds is None else seconds * 1000


def load_level(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ok = [r for r in rows if r["status"] == 200 and not r["error"]]
    rejected = [r for r in rows if r["status"] in REJECTED]
    timeouts = [r for r in rows if r["status"] == 504 or r["error"] == "timeout_error"]
    other = len(rows) - len(ok) - len(rejected) - len(timeouts)

    ttft = [r["ttft_s"] for r in ok if r["ttft_s"] is not None]
    latency = [r["total_s"] for r in ok]
    # Tokens after the first one, over the time they took: the engine's generation speed.
    speed = [(r["completion_tokens"] - 1) / (r["total_s"] - r["ttft_s"])
             for r in ok if r["ttft_s"] is not None and r["total_s"] > r["ttft_s"]
             and r["completion_tokens"] > 1]

    wall = None
    if rows:
        wall = max(r["start"] + r["total_s"] for r in rows) - min(r["start"] for r in rows)
    tokens = sum(r["completion_tokens"] or 0 for r in ok)

    return {
        "attempts": len(rows), "ok": len(ok), "rejected": len(rejected),
        "rejected_429": sum(1 for r in rejected if r["status"] == 429),
        # how long a client waits to be told "queue full": the cost of saying no
        "refused_p95_ms": ms(percentile([r["total_s"] for r in rejected if r["status"] == 429],
                                        95)),
        "rejected_503": sum(1 for r in rejected if r["status"] == 503),
        "timeouts": len(timeouts), "other_errors": other,
        "rejected_pct": 100 * len(rejected) / len(rows) if rows else 0.0,
        "tokens_per_s": tokens / wall if wall else None,
        "req_per_s": len(ok) / wall if wall else None,
        "ttft_p50": percentile(ttft, 50), "ttft_p95": percentile(ttft, 95),
        "ttft_p99": percentile(ttft, 99),
        "lat_p50": percentile(latency, 50), "lat_p95": percentile(latency, 95),
        "lat_p99": percentile(latency, 99),
        "speed_p50": percentile(speed, 50),
    }


def load_all() -> dict[str, dict[int, dict]]:
    data: dict[str, dict[int, dict]] = {}
    for path in sorted(RESULTS.glob("*_c*.jsonl")):
        match = re.fullmatch(r"(.+)_c(\d+)\.jsonl", path.name)
        if match and match.group(1) in CONFIGS:
            data.setdefault(match.group(1), {})[int(match.group(2))] = load_level(path)
    return {c: dict(sorted(levels.items())) for c, levels in data.items()}


def fmt(value, digits=2, suffix="") -> str:
    return "-" if value is None else f"{value:.{digits}f}{suffix}"


def turned_away(m: dict) -> str:
    """'350 x 429 (94%)': how many requests the service refused, and why."""
    if not m["rejected"]:
        return "0"
    parts = [f"{m[key]} x {code}" for key, code in (("rejected_429", 429), ("rejected_503", 503))
             if m[key]]
    return f"{' + '.join(parts)} ({m['rejected_pct']:.0f}%)"


MODEL_FILES = {
    "1b-q4": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "1b-q8": "Llama-3.2-1B-Instruct-Q8_0.gguf",
    "3b-q4": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
}


def memory_table() -> list[str]:
    lines = ["| Configuration | Model file (the weights) | Engine, `docker stats` | Gateway |",
             "|---|---|---|---|"]

    def mib(text: str) -> float:
        number = float(re.match(r"[\d.]+", text).group())
        return number * 1024 if "GiB" in text.split("/")[0] else number

    for config, (label, _) in CONFIGS.items():
        path = RESULTS / f"{config}_memory.json"
        model = HERE.parent / "models" / MODEL_FILES[config]
        if not path.exists():
            continue
        usage = {m["container"]: mib(m["memory"]) for m in json.loads(path.read_text())}
        engine = next((v for k, v in usage.items() if "llama" in k), 0)
        gateway = next((v for k, v in usage.items() if "gateway" in k), 0)
        size = f"{model.stat().st_size / 1024 ** 3:.2f} GiB" if model.exists() else "-"
        lines.append(f"| {label} | {size} | {engine / 1024:.2f} GiB | {gateway:.0f} MiB |")
    return lines


def write_report(data: dict) -> None:
    lines = [
        "# Benchmark report", "",
        "_Generated by `benchmarks/analyze.py` from the raw results in `benchmarks/results/`._", "",
        f"**Machine:** {MACHINE}", "",
        "**Load:** closed loop. Each simulated user sends a streamed request, waits for the "
        "whole answer and sends the next one (users honor `Retry-After` when turned away). "
        "Every answer asks for 64 tokens at temperature 0; about 1 prompt in 17 is a longer one "
        "(a few hundred tokens). The gateway keeps its production limits: 2 running, 8 queued, "
        "30 s maximum wait in the queue. Per-client rate limiting is off.", "",
        "**How to read it:** time to first token (TTFT) and latency include the time a request "
        "spent waiting in the queue, because that is what a client experiences. "
        "Percentiles use only successful requests; `n` is how many there were, and with fewer "
        "than 100 the p99 is close to the slowest request. "
        "Speed is tokens per second of one request after its first token. "
        "`429` means the queue was full; `503` means the request waited more than the maximum "
        "time in the queue. Turned-away users retry after the `Retry-After` the server sends, so "
        "at 16 users the share of refused attempts is inflated by those retries. "
        "With so few requests per level, small differences (especially time to first token at 1 "
        "and 2 users, which mostly reflects whether a long prompt happened to be in the sample) "
        "are noise.", "",
    ]

    for config, (label, _) in CONFIGS.items():
        if config not in data:
            continue
        lines += [f"## {label}", "",
                  "| Users | n ok | Turned away | 429 answered in (p95) | Timeouts | "
                  "Other errors | Tokens/s (all) | Speed per request (tok/s) | "
                  "TTFT p50 / p95 / p99 (s) | Latency p50 / p95 / p99 (s) |",
                  "|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
        for users, m in data[config].items():
            lines.append(
                f"| {users} | {m['ok']} | {turned_away(m)} | {fmt(m['refused_p95_ms'], 0, ' ms')} | "
                f"{m['timeouts']} | {m['other_errors']} | "
                f"{fmt(m['tokens_per_s'], 1)} | {fmt(m['speed_p50'], 1)} | "
                f"{fmt(m['ttft_p50'])} / {fmt(m['ttft_p95'])} / {fmt(m['ttft_p99'])} | "
                f"{fmt(m['lat_p50'])} / {fmt(m['lat_p95'])} / {fmt(m['lat_p99'])} |")
        lines.append("")

    lines += ["## Side by side", ""]
    for title, key, digits in (("Tokens per second, all users together", "tokens_per_s", 1),
                               ("TTFT p95 (s)", "ttft_p95", 2),
                               ("Latency p95 (s)", "lat_p95", 1),
                               ("Speed of one request (tokens/s)", "speed_p50", 1)):
        levels = sorted({u for c in data.values() for u in c})
        lines += [f"**{title}**", "",
                  "| Users | " + " | ".join(CONFIGS[c][0] for c in CONFIGS if c in data) + " |",
                  "|---:|" + "---:|" * len([c for c in CONFIGS if c in data])]
        for users in levels:
            cells = [fmt(data[c].get(users, {}).get(key), digits) for c in CONFIGS if c in data]
            lines.append(f"| {users} | " + " | ".join(cells) + " |")
        lines.append("")

    memory = memory_table()
    if len(memory) > 2:
        lines += ["## Memory", "",
                  "The weights have to be resident to generate, so the model file size is the "
                  "reliable floor for each configuration. The `docker stats` column is "
                  "**not** a footprint: llama.cpp memory-maps the model and `docker stats` does "
                  "not count those file-backed pages, so it understates (it even shows the "
                  "larger Q8 file as using less than Q4). It is kept only as a record of what "
                  "that tool reports.", ""] + memory
        lines.append("")

    lines += ["![Comparison](benchmark.png)", ""]
    (HERE / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def write_csv(data: dict) -> None:
    fields = ["config", "users", "attempts", "ok", "rejected", "rejected_429", "rejected_503",
              "refused_p95_ms",
              "timeouts", "other_errors",
              "tokens_per_s", "req_per_s", "speed_p50", "ttft_p50", "ttft_p95", "ttft_p99",
              "lat_p50", "lat_p95", "lat_p99"]
    with open(RESULTS / "summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for config, levels in data.items():
            for users, m in levels.items():
                writer.writerow({"config": config, "users": users, **m})


def draw_chart(data: dict) -> None:
    panels = [
        ("Tokens generated per second (all users)", "tokens_per_s", "tokens / s"),
        ("Time to first token, p95", "ttft_p95", "seconds"),
        ("Request latency, p95", "lat_p95", "seconds"),
        ("Requests turned away", "rejected_pct", "% of attempts"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.2), facecolor=SURFACE)
    levels = sorted({u for c in data.values() for u in c})

    for ax, (title, key, ylabel) in zip(axes.flat, panels):
        ax.set_facecolor(SURFACE)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(GRID)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=9, length=0)
        ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
        ax.set_ylabel(ylabel, fontsize=9, color=INK_2)
        ax.set_xlabel("concurrent users", fontsize=9, color=INK_2)
        ax.set_xscale("log", base=2)
        ax.set_xticks(levels, [str(u) for u in levels])
        ax.minorticks_off()

        ends = []  # (config, last x, last y) for the direct labels
        for config, (label, color) in CONFIGS.items():
            if config not in data:
                continue
            xs = [u for u in levels if data[config].get(u, {}).get(key) is not None]
            ys = [data[config][u][key] for u in xs]
            ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=7,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=label, clip_on=False)
            if xs:
                ends.append((label, color, xs[-1], ys[-1]))

        # Set the limits after plotting: setting them first would switch autoscaling off.
        ax.set_xlim(0.85, 30)
        ax.margins(y=0.1)
        ax.set_ylim(bottom=0)
        label_ends(ax, ends)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=len(labels), frameon=False,
               fontsize=10, labelcolor=INK, bbox_to_anchor=(0.98, 0.99))
    fig.suptitle("tokenscope: three model configurations under growing load", x=0.02, y=0.99,
                 ha="left", va="top", fontsize=13, color=INK)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.86, bottom=0.08, hspace=0.42, wspace=0.2)
    fig.savefig(HERE / "benchmark.png", dpi=150, facecolor=SURFACE)


def label_ends(ax, ends: list) -> None:
    """Name each line at its right end, nudged apart so two labels never overlap."""
    if not ends:
        return
    ends = sorted(ends, key=lambda e: e[3])
    low, high = ax.get_ylim()
    gap = (high - low) * 0.075
    ys = [e[3] for e in ends]
    for _ in range(50):  # push neighbours apart, half each way, until they have room
        moved = False
        for i in range(1, len(ys)):
            short = gap - (ys[i] - ys[i - 1])
            if short > 1e-9:
                ys[i - 1] -= short / 2
                ys[i] += short / 2
                moved = True
        if not moved:
            break
    shift = max(0.0, low + gap / 2 - ys[0]) - max(0.0, ys[-1] - (high - gap / 2))
    ys = [y + shift for y in ys]  # keep the whole group inside the plot

    for (label, color, x, y), target in zip(ends, ys):
        ax.annotate(label, xy=(x, y), xytext=(x * 1.12, target), color=INK_2, fontsize=8.5,
                    va="center", ha="left", annotation_clip=False,
                    arrowprops={"arrowstyle": "-", "color": color, "linewidth": 0.8,
                                "shrinkA": 0, "shrinkB": 5} if abs(target - y) > 1e-6 else None)


def main() -> None:
    data = load_all()
    if not data:
        raise SystemExit("no results found, run benchmarks/run_benchmark.py first")
    write_csv(data)
    write_report(data)
    draw_chart(data)
    print("wrote benchmarks/REPORT.md, benchmarks/benchmark.png and results/summary.csv")


if __name__ == "__main__":
    main()
