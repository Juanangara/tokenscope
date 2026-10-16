"""Closed-loop load for tokenscope: every simulated user sends a streamed chat request, waits for
the whole answer, and immediately sends the next one.

Each finished request is written as one JSON line to BENCH_OUT, with the numbers Locust does not
measure by itself (time to first token, tokens generated). benchmarks/analyze.py reads those files.

Environment: BENCH_OUT (required), BENCH_API_KEY (optional), BENCH_MAX_TOKENS (default 64).
"""
import itertools
import json
import os
import time

from locust import HttpUser, constant, events, task

MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "64"))
API_KEY = os.environ.get("BENCH_API_KEY")

# Prompts that make the model keep writing, so every answer uses its whole token budget and
# configurations are compared on the same amount of work. Mostly short prompts, plus one
# longer one (a few hundred tokens, to exercise prompt processing) every 17 requests.
SHORT = [
    "Write a story about a lighthouse keeper who finds a message in a bottle.",
    "Explain how a rainbow forms, step by step, in simple words.",
    "Describe a busy market in a small coastal town in great detail.",
    "Write a letter from an astronaut to a friend back on Earth.",
]
LONG_CONTEXT = (
    "The city council met on Tuesday to discuss the new transport plan. "
    "Several residents spoke about bus routes, bicycle lanes and parking limits. "
) * 12
LONG = [f"Read this report and then write a detailed summary of it.\n\n{LONG_CONTEXT}"]
PROMPTS = SHORT + SHORT + SHORT + SHORT + LONG  # 16 short : 1 long, in a fixed order

_out = None


@events.test_start.add_listener
def open_output(environment, **_):
    global _out
    path = os.environ["BENCH_OUT"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _out = open(path, "w", encoding="utf-8")


@events.test_stop.add_listener
def close_output(environment, **_):
    if _out:
        _out.close()


def record(**fields) -> None:
    _out.write(json.dumps(fields) + "\n")
    _out.flush()


class ChatUser(HttpUser):
    wait_time = constant(0)
    _next_user = itertools.count()

    def on_start(self):
        # Each user starts at a different place in the prompt list, then cycles through it:
        # the mix is the same on every run, with no randomness.
        start = next(ChatUser._next_user)
        self._prompts = itertools.islice(itertools.cycle(PROMPTS), start, None)
        self.headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}

    @task
    def chat(self):
        prompt = next(self._prompts)
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "stream": True,
        }
        started = time.time()
        t0 = time.perf_counter()
        ttft = None
        usage = None
        pieces = 0
        error = None
        retry_after = 0.0

        with self.client.post("/v1/chat/completions", json=body, headers=self.headers,
                              stream=True, catch_response=True, name="chat (stream)") as resp:
            status = resp.status_code
            if status != 200:
                retry_after = float(resp.headers.get("Retry-After", 1))
                resp.failure(f"HTTP {status}")
            else:
                for line in resp.iter_lines():
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[len(b"data:"):].strip()
                    if payload == b"[DONE]":
                        break
                    event = json.loads(payload)
                    if "error" in event:  # the stream broke after the 200 was sent
                        error = event["error"]["type"]
                        break
                    choices = event.get("choices") or []
                    if choices and choices[0]["delta"].get("content"):
                        pieces += 1
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                    if event.get("usage"):
                        usage = event["usage"]
                if error:
                    resp.failure(error)

        record(
            start=started,
            status=status,
            error=error,
            prompt_chars=len(prompt),
            total_s=time.perf_counter() - t0,
            ttft_s=ttft,
            prompt_tokens=usage["prompt_tokens"] if usage else None,
            completion_tokens=usage["completion_tokens"] if usage else pieces,
        )

        if status != 200:
            # A well-behaved client does what the server asks instead of hammering it.
            time.sleep(retry_after)
