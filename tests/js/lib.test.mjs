// Run with: node --test tests/js
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  SseParser,
  describeHttpError,
  formatLoad,
  formatStats,
  interpretEvent,
  parseMetrics,
} from "../../demo/lib.js";

describe("SseParser", () => {
  it("returns the payload of each complete event", () => {
    const parser = new SseParser();

    assert.deepEqual(parser.push('data: {"a":1}\n\ndata: {"b":2}\n\n'), ['{"a":1}', '{"b":2}']);
  });

  it("waits for the rest of an event that arrives in pieces", () => {
    const parser = new SseParser();

    assert.deepEqual(parser.push('data: {"a"'), []);
    assert.deepEqual(parser.push(":1}\n"), []);
    assert.deepEqual(parser.push("\n"), ['{"a":1}']);
  });

  it("copes with CRLF line endings, even split between two chunks", () => {
    const parser = new SseParser();

    assert.deepEqual(parser.push("data: one\r"), []);
    assert.deepEqual(parser.push("\n\r\ndata: two\r\n\r\n"), ["one", "two"]);
  });

  it("ignores lines that are not data (comments, event names)", () => {
    const parser = new SseParser();

    assert.deepEqual(parser.push(": keep-alive\nevent: x\ndata: hi\n\n"), ["hi"]);
  });

  it("does not emit empty events", () => {
    assert.deepEqual(new SseParser().push("\n\n\n\n"), []);
  });
});

describe("interpretEvent", () => {
  it("recognizes the end of the stream", () => {
    assert.deepEqual(interpretEvent("[DONE]"), { type: "done" });
  });

  it("reads the text of a chunk", () => {
    const payload = JSON.stringify({ choices: [{ delta: { content: "Hel" }, finish_reason: null }] });

    assert.deepEqual(interpretEvent(payload), {
      type: "chunk", delta: "Hel", finishReason: null, usage: null,
    });
  });

  it("treats the role-only first chunk as an empty piece", () => {
    const payload = JSON.stringify({ choices: [{ delta: { role: "assistant" } }] });

    assert.equal(interpretEvent(payload).delta, "");
  });

  it("reads the finish reason and the usage", () => {
    const stop = interpretEvent(JSON.stringify({ choices: [{ delta: {}, finish_reason: "stop" }] }));
    const usage = interpretEvent(JSON.stringify({
      choices: [{ delta: {}, finish_reason: null }],
      usage: { prompt_tokens: 40, completion_tokens: 64 },
    }));

    assert.equal(stop.finishReason, "stop");
    assert.equal(usage.usage.completion_tokens, 64);
  });

  it("reports an error sent inside the stream", () => {
    const payload = JSON.stringify({ error: { message: "engine broke", type: "service_unavailable" } });

    assert.deepEqual(interpretEvent(payload), { type: "error", message: "engine broke" });
  });

  it("ignores payloads that are not JSON", () => {
    assert.deepEqual(interpretEvent("not json"), { type: "ignored" });
  });

  it("copes with a chunk without choices", () => {
    assert.equal(interpretEvent("{}").delta, "");
  });
});

describe("parseMetrics", () => {
  const text = [
    "# HELP tokenscope_active_requests Requests being served.",
    "# TYPE tokenscope_active_requests gauge",
    "tokenscope_active_requests 2.0",
    "tokenscope_queued_requests 3.0",
    "tokenscope_engine_slots 2.0",
    'tokenscope_requests_total{outcome="ok",stream="true"} 7.0',
  ].join("\n");

  it("reads the live queue state", () => {
    assert.deepEqual(parseMetrics(text), { active: 2, queued: 3, slots: 2 });
  });

  it("returns null when the gauges are not there", () => {
    assert.equal(parseMetrics("something_else 1.0"), null);
  });

  it("does not confuse a gauge with a longer metric that starts the same way", () => {
    const tricky = "tokenscope_active_requests_total 9.0\n";

    assert.equal(parseMetrics(tricky), null);
  });
});

describe("describeHttpError", () => {
  it("retries a busy service after the time the server asks for", () => {
    const info = describeHttpError(429, "queue is full", "3");

    assert.equal(info.retry, true);
    assert.equal(info.retryAfter, 3);
    assert.match(info.message, /busy/);
  });

  it("waits one second when the server gives no usable Retry-After", () => {
    assert.equal(describeHttpError(429, "", null).retryAfter, 1);
    assert.equal(describeHttpError(429, "", "soon").retryAfter, 1);
    assert.equal(describeHttpError(429, "", "0").retryAfter, 1);
  });

  it("asks for the API key on 401", () => {
    const info = describeHttpError(401, "invalid API key");

    assert.equal(info.needsKey, true);
    assert.equal(info.retry, false);
  });

  it("does not retry anything else", () => {
    for (const status of [400, 503, 504, 500]) {
      assert.equal(describeHttpError(status, "x").retry, false, `status ${status}`);
    }
  });

  it("includes the server's explanation", () => {
    assert.match(describeHttpError(400, "prompt is too long").message, /prompt is too long/);
  });

  it("has a message for unexpected statuses", () => {
    assert.match(describeHttpError(418, "").message, /HTTP 418/);
  });
});

describe("formatStats", () => {
  it("shows first-token time, speed and token count", () => {
    // 64 tokens: the first after 310 ms, the other 63 in 4.5 s -> 14 tok/s
    const text = formatStats({ ttftMs: 310, tokens: 64, generationMs: 4500 });

    assert.equal(text, "TTFT 0.31 s · 14.0 tok/s · 64 tokens");
  });

  it("leaves the speed out when it cannot be told", () => {
    assert.equal(formatStats({ ttftMs: 200, tokens: 1, generationMs: 0 }), "TTFT 0.20 s · 1 token");
  });

  it("copes with an answer that never produced text", () => {
    assert.equal(formatStats({ ttftMs: null, tokens: 0, generationMs: 0 }), "");
  });
});

describe("formatLoad", () => {
  it("summarizes slots and queue", () => {
    assert.equal(formatLoad({ active: 2, queued: 3, slots: 2 }), "2/2 slots busy · 3 queued");
  });
});
