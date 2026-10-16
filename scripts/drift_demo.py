"""Shows prompt drift detection working, against a running stack.

It sends normal traffic (mostly short English prompts) until the service has learned its
reference and filled a comparison window, then switches to a clear shift (long prompts in
Spanish) and prints the drift scores as the window fills with the new traffic.

    python scripts/drift_demo.py
    python scripts/drift_demo.py --prometheus http://127.0.0.1:9090 --wait-alert

The reference is learned from the first prompts the gateway ever sees, so start from a fresh one
(docker compose exec gateway rm /data/drift-reference.json, then restart the gateway) or the
scores will be relative to whatever traffic came first. Answers are cut to one token: only the
prompts matter here.
"""
import argparse
import asyncio
import random
import re
import time

import httpx

# Short, medium and long prompts are built from these sentences.
ENGLISH = [
    "The city council met on Tuesday to discuss the new transport plan.",
    "Several residents spoke about bus routes, bicycle lanes and parking limits.",
    "A local bakery announced that it will open a second shop near the river.",
    "The weather forecast says it will rain for most of the weekend.",
    "Engineers explained how the new bridge will handle heavy traffic.",
    "The school is organizing a science fair for the students next month.",
    "Farmers in the valley expect a good harvest after a mild winter.",
    "A new library will open its doors to the public in the spring.",
]
ENGLISH_SHORT = [
    "What is the capital of France?", "Tell me a joke.", "Explain recursion.",
    "How do I boil an egg?", "Define entropy.", "Say hello in Italian.",
]
SPANISH = [
    "El ayuntamiento se reunio el martes para discutir el nuevo plan de transporte publico.",
    "Varios vecinos hablaron sobre las rutas de autobus, los carriles bici y el aparcamiento.",
    "Una panaderia local anuncio que abrira una segunda tienda cerca del rio la proxima semana.",
    "El pronostico del tiempo dice que llovera durante la mayor parte del fin de semana.",
    "Los ingenieros explicaron como el nuevo puente soportara el trafico pesado de camiones.",
    "La escuela organiza una feria de ciencias para los estudiantes el mes que viene.",
    "Los agricultores del valle esperan una buena cosecha despues de un invierno suave.",
    "Una nueva biblioteca abrira sus puertas al publico durante la primavera de este ano.",
]

# (low, high) characters of each length bucket used by the service
BUCKETS = [(10, 49), (50, 149), (150, 499), (500, 1999), (2000, 2600)]
NORMAL = [0.30, 0.35, 0.20, 0.10, 0.05]
SHIFTED = [0.05, 0.10, 0.20, 0.35, 0.30]


def make_prompt(rng: random.Random, weights: list[float], sentences: list[str],
                short: list[str] | None = None) -> str:
    low, high = BUCKETS[rng.choices(range(len(BUCKETS)), weights)[0]]
    target = rng.randint(low, high)
    if target < 50 and short:
        return rng.choice(short)
    text = ""
    while len(text) < target:
        text += rng.choice(sentences) + " "
    return text[:target].strip()


def read_state(metrics_text: str) -> dict:
    def value(name: str, labels: str = ""):
        match = re.search(rf"^{name}{re.escape(labels)}\s+(\S+)$", metrics_text, re.M)
        return float(match.group(1)) if match else None

    return {
        "ready": value("tokenscope_prompt_drift_ready"),
        "reference": value("tokenscope_prompt_drift_reference_samples"),
        "window": value("tokenscope_prompt_drift_window_samples"),
        "threshold": value("tokenscope_prompt_drift_threshold"),
        "length": value("tokenscope_prompt_drift_score", '{feature="length"}'),
        "language": value("tokenscope_prompt_drift_score", '{feature="language"}'),
    }


def describe(state: dict) -> str:
    if state["ready"] is None:
        return "drift metrics not found (is DRIFT_ENABLED on?)"
    if not state["ready"]:
        if state["window"]:
            return f"reference learned; window {state['window']:.0f} prompts, not enough yet"
        return f"learning the reference: {state['reference']:.0f} prompts so far"

    def flag(score):
        return f"{score:.2f}" + ("  <- ABOVE THRESHOLD" if score > state["threshold"] else "")

    return (f"window {state['window']:.0f} | length PSI {flag(state['length'])} | "
            f"language PSI {flag(state['language'])}")


async def run_phase(name: str, count: int, make, client: httpx.AsyncClient, gateway: str,
                    headers: dict, concurrency: int, report_every: int) -> None:
    print(f"\n== {name}: {count} requests", flush=True)
    gate = asyncio.Semaphore(concurrency)
    done = 0

    async def send(prompt: str) -> None:
        nonlocal done
        body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 1,
                "temperature": 0}
        async with gate:
            for _ in range(60):
                response = await client.post(f"{gateway}/v1/chat/completions", json=body,
                                             headers=headers)
                if response.status_code == 429:  # rate limit or busy: do what the server asks
                    await asyncio.sleep(float(response.headers.get("Retry-After", 1)))
                    continue
                response.raise_for_status()
                break
        done += 1
        if done % report_every == 0 or done == count:
            text = (await client.get(f"{gateway}/metrics")).text
            print(f"  {done:>4}/{count}  {describe(read_state(text))}", flush=True)

    await asyncio.gather(*(send(make()) for _ in range(count)))


async def wait_for_alert(client: httpx.AsyncClient, prometheus: str, timeout_s: int) -> None:
    print(f"\n== waiting up to {timeout_s}s for the PromptDrift alert (it needs 2 minutes "
          "above the threshold)", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        alerts = (await client.get(f"{prometheus}/api/v1/alerts")).json()["data"]["alerts"]
        mine = [a for a in alerts if a["labels"].get("alertname") == "PromptDrift"]
        states = sorted({(a["labels"].get("feature"), a["state"]) for a in mine})
        print(f"  alerts: {states or 'none yet'}", flush=True)
        if any(state == "firing" for _, state in states):
            return
        await asyncio.sleep(15)
    print("  the alert did not fire in time")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    parser.add_argument("--prometheus", help="e.g. http://127.0.0.1:9090, to show the alert")
    parser.add_argument("--api-key")
    parser.add_argument("--reference", type=int, default=100, help="DRIFT_REFERENCE_SIZE")
    parser.add_argument("--window", type=int, default=100, help="DRIFT_WINDOW_SIZE")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--wait-alert", action="store_true")
    args = parser.parse_args()

    rng = random.Random(2024)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    async with httpx.AsyncClient(timeout=120) as client:
        state = read_state((await client.get(f"{args.gateway}/metrics")).text)
        print("at start:", describe(state))
        if state["reference"] and state["reference"] >= args.reference:
            print("note: a reference already exists, so scores are relative to it, not to this "
                  "script's baseline traffic")

        await run_phase("normal traffic (reference, then a stable window)",
                        args.reference + args.window,
                        lambda: make_prompt(rng, NORMAL, ENGLISH, ENGLISH_SHORT),
                        client, args.gateway, headers, args.concurrency, 25)
        await run_phase("shift: long prompts in Spanish", args.window,
                        lambda: make_prompt(rng, SHIFTED, SPANISH),
                        client, args.gateway, headers, args.concurrency, 25)

        if args.prometheus and args.wait_alert:
            await wait_for_alert(client, args.prometheus, 240)


if __name__ == "__main__":
    asyncio.run(main())
