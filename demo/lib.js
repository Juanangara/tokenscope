// Pure helpers for the demo page. No DOM here, so they can be tested with `node --test`.

/** Splits a server-sent-events byte stream into complete `data:` payloads. */
export class SseParser {
  constructor() {
    this.buffer = "";
  }

  /** Feed the next piece of text; returns the payloads of every event that is now complete. */
  push(text) {
    // Normalize on the whole buffer, so a "\r\n" split between two chunks is still recognized.
    this.buffer = (this.buffer + text).replace(/\r\n/g, "\n");
    const payloads = [];
    let end;
    while ((end = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice("data:".length).trimStart())
        .join("\n");
      if (data) payloads.push(data);
    }
    return payloads;
  }
}

/** Turns one `data:` payload into {type: "done" | "error" | "chunk" | "ignored", ...}. */
export function interpretEvent(payload) {
  if (payload === "[DONE]") return { type: "done" };

  let event;
  try {
    event = JSON.parse(payload);
  } catch {
    return { type: "ignored" };
  }

  if (event.error) {
    return { type: "error", message: event.error.message || "The service reported an error." };
  }
  const choice = (event.choices || [])[0];
  return {
    type: "chunk",
    delta: (choice && choice.delta && choice.delta.content) || "",
    finishReason: (choice && choice.finish_reason) || null,
    usage: event.usage || null,
  };
}

/** Reads the live queue state out of the Prometheus text exposed at /metrics. */
export function parseMetrics(text) {
  const read = (name) => {
    const match = text.match(new RegExp(`^${name}\\s+([0-9.eE+-]+)\\s*$`, "m"));
    return match ? Number(match[1]) : null;
  };
  const active = read("tokenscope_active_requests");
  const queued = read("tokenscope_queued_requests");
  const slots = read("tokenscope_engine_slots");
  if (active === null || queued === null || slots === null) return null;
  return { active, queued, slots };
}

/**
 * Decides what the page does about a failed request.
 * Only `429` is retried: it means "too busy right now", and the server says when to come back.
 */
export function describeHttpError(status, serverMessage, retryAfterHeader) {
  const detail = serverMessage ? ` ${serverMessage}` : "";
  switch (status) {
    case 400:
      return { message: `The service rejected the request:${detail}`, retry: false };
    case 401:
      return { message: `Authentication failed:${detail}`, retry: false, needsKey: true };
    case 429: {
      const seconds = Number(retryAfterHeader);
      return {
        message: `The service is busy:${detail}`,
        retry: true,
        retryAfter: Number.isFinite(seconds) && seconds > 0 ? seconds : 1,
      };
    }
    case 503:
      return { message: `The service is unavailable:${detail}`, retry: false };
    case 504:
      return { message: `The answer took too long:${detail}`, retry: false };
    default:
      return { message: `Unexpected error (HTTP ${status}).${detail}`, retry: false };
  }
}

/**
 * "TTFT 0.31 s · 14.2 tok/s · 64 tokens". Speed counts the tokens after the first one over the
 * time they took, the same definition the benchmark uses; it is left out when it cannot be told.
 */
export function formatStats({ ttftMs, tokens, generationMs }) {
  const parts = [];
  if (ttftMs !== null && ttftMs !== undefined) parts.push(`TTFT ${(ttftMs / 1000).toFixed(2)} s`);
  if (tokens > 1 && generationMs > 0) {
    parts.push(`${(((tokens - 1) / generationMs) * 1000).toFixed(1)} tok/s`);
  }
  if (tokens > 0) parts.push(`${tokens} ${tokens === 1 ? "token" : "tokens"}`);
  return parts.join(" · ");
}

/** "2/2 slots busy · 3 queued" */
export function formatLoad({ active, queued, slots }) {
  return `${active}/${slots} slots busy · ${queued} queued`;
}
