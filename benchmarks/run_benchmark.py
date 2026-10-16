"""Runs the load test for several model configurations and concurrency levels.

For each configuration it restarts the engine and the gateway with that model, waits until the
service is ready, warms it up, and then runs Locust at each concurrency level, writing one
JSONL file per level to results/. Afterwards, run benchmarks/analyze.py.

    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --levels 1,2,4 --duration 30 --only 1b-q4
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
# Not "localhost": on Windows it resolves to IPv6 first, Docker only listens on IPv4, and every
# new connection then waits ~2 s for the refused attempt, which would be added to every latency.
GATEWAY = "http://127.0.0.1:8000"

# name -> model file in ./models
CONFIGS = {
    "1b-q4": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "1b-q8": "Llama-3.2-1B-Instruct-Q8_0.gguf",
    "3b-q4": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
}
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml",
           "-f", "benchmarks/docker-compose.bench.yml"]


def run(cmd: list[str], env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, check=True,
                          **kwargs)


def wait_until_ready(timeout_s: int = 240) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{GATEWAY}/ready", timeout=3) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    sys.exit("the service did not become ready in time")


def warm_up() -> None:
    body = json.dumps({"messages": [{"role": "user", "content": "Say hello."}],
                       "max_tokens": 16, "temperature": 0}).encode()
    for _ in range(2):
        request = urllib.request.Request(
            f"{GATEWAY}/v1/chat/completions", body, {"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=120).read()


def memory_snapshot(config: str) -> None:
    """Memory used by each container, right after the heaviest load of this configuration."""
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                         capture_output=True, text=True, check=True).stdout
    stats = [json.loads(line) for line in out.splitlines() if "tokenscope" in line]
    (RESULTS / f"{config}_memory.json").write_text(json.dumps(
        [{"container": s["Name"], "memory": s["MemUsage"]} for s in stats], indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="1,2,4,8,16", help="concurrent users per run")
    parser.add_argument("--duration", type=int, default=60, help="seconds per level")
    parser.add_argument("--only", default="", help="comma-separated configurations to run")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",")]
    wanted = [c for c in args.only.split(",") if c] or list(CONFIGS)
    RESULTS.mkdir(parents=True, exist_ok=True)

    for config in wanted:
        model_file = CONFIGS[config]
        if not (ROOT / "models" / model_file).exists():
            sys.exit(f"missing models/{model_file}, see scripts/download_benchmark_models.sh")

        print(f"\n=== {config} ({model_file}) ===", flush=True)
        run(COMPOSE + ["up", "-d", "--force-recreate", "llama", "gateway"],
            env={"MODEL_FILE": model_file})
        wait_until_ready()
        warm_up()

        for users in levels:
            out = RESULTS / f"{config}_c{users}.jsonl"
            print(f"--- {users} concurrent users for {args.duration}s", flush=True)
            run([sys.executable, "-m", "locust", "-f", "benchmarks/locustfile.py", "--headless",
                 "--host", GATEWAY, "-u", str(users), "-r", str(users),
                 "-t", f"{args.duration}s",
                 # let requests that started inside the window finish, so none is cut short
                 "--stop-timeout", "150", "--only-summary",
                 # turned-away requests count as Locust failures, but under overload they are
                 # the expected result, not a reason to stop the benchmark
                 "--exit-code-on-error", "0"],
                env={"BENCH_OUT": str(out)})
            time.sleep(5)  # let the queue drain before the next level

        memory_snapshot(config)

    print("\nDone. Next: python benchmarks/analyze.py", flush=True)


if __name__ == "__main__":
    main()
