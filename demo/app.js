import {
  SseParser,
  describeHttpError,
  formatLoad,
  formatStats,
  interpretEvent,
  parseMetrics,
} from "./lib.js";

const CHAT_URL = "/v1/chat/completions";
const MAX_RETRIES = 3; // how many times a "busy" answer is retried before giving up

const $ = (id) => document.getElementById(id);
const log = $("log");
const form = $("composer");
const promptBox = $("prompt");
const sendButton = $("send");
const stopButton = $("stop");
const keyInput = $("api-key");
const maxTokensInput = $("max-tokens");

let history = []; // what the model is sent: [{role, content}]
let controller = null; // aborts the running request; aborting also stops the engine

// ---------------------------------------------------------------- conversation view

function addMessage(role, text = "") {
  $("empty")?.remove();
  const element = document.createElement("article");
  element.className = `msg ${role}`;

  const body = document.createElement("div");
  body.className = "body";
  body.textContent = text; // textContent, never innerHTML: model output is untrusted

  const stats = document.createElement("div");
  stats.className = "stats";

  element.append(body, stats);
  log.append(element);
  scrollDown();
  return { body, stats };
}

function scrollDown() {
  window.scrollTo({ top: document.body.scrollHeight });
}

function setBody(view, text, kind = "") {
  view.body.textContent = text;
  view.body.className = `body ${kind}`.trim();
}

function setBusy(busy) {
  sendButton.hidden = busy;
  stopButton.hidden = !busy;
  promptBox.disabled = busy;
  if (!busy) promptBox.focus();
}

// ---------------------------------------------------------------- talking to the service

function requestHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (keyInput.value.trim()) headers.Authorization = `Bearer ${keyInput.value.trim()}`;
  return headers;
}

function maxTokens() {
  const value = Number(maxTokensInput.value);
  return Number.isInteger(value) && value > 0 ? value : 200;
}

async function errorFromResponse(response) {
  let message = "";
  try {
    message = (await response.json()).error?.message ?? "";
  } catch {
    /* the body was not JSON; the status is enough */
  }
  return describeHttpError(response.status, message, response.headers.get("Retry-After"));
}

/** Waits `seconds`, showing a countdown. Returns false if the user pressed Stop meanwhile. */
async function countdown(view, info, attempt, signal) {
  for (let left = info.retryAfter; left > 0; left--) {
    setBody(view, `${info.message}\nTrying again in ${left} s (attempt ${attempt} of ${MAX_RETRIES})…`,
      "notice");
    await new Promise((resolve) => setTimeout(resolve, 1000));
    if (signal.aborted) return false;
  }
  return !signal.aborted;
}

async function sendMessage(text) {
  // Keep this conversation's own list: if "New chat" is pressed while this request is being
  // stopped, whatever finishes late must not land in the new conversation.
  const conversation = history;
  conversation.push({ role: "user", content: text });
  addMessage("user", text);
  const view = addMessage("assistant");
  setBody(view, "", "waiting");

  controller = new AbortController();
  setBusy(true);
  try {
    const answer = await streamAnswer(conversation, view, controller.signal);
    if (answer) conversation.push({ role: "assistant", content: answer });
    else conversation.pop(); // nothing came back: do not leave an unanswered question behind
  } finally {
    controller = null;
    setBusy(false);
  }
}

/** Returns the text of the answer ("" when there was none). */
async function streamAnswer(conversation, view, signal) {
  for (let attempt = 1; ; attempt++) {
    const startedAt = performance.now();
    let response;
    try {
      response = await fetch(CHAT_URL, {
        method: "POST",
        headers: requestHeaders(),
        body: JSON.stringify({ messages: conversation, max_tokens: maxTokens(), stream: true }),
        signal,
      });
    } catch {
      if (signal.aborted) setBody(view, "Stopped.", "notice");
      else setBody(view, "Could not reach the service.", "error");
      return "";
    }

    if (response.ok) return readStream(response, view, startedAt, signal);

    const info = await errorFromResponse(response);
    if (info.retry && attempt <= MAX_RETRIES) {
      if (await countdown(view, info, attempt, signal)) {
        setBody(view, "", "waiting");
        continue;
      }
      setBody(view, "Stopped.", "notice");
      return "";
    }
    setBody(view, info.message, "error");
    if (info.needsKey) {
      keyInput.closest("details").open = true;
      keyInput.focus();
    }
    return "";
  }
}

async function readStream(response, view, startedAt, signal) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();

  let text = "";
  let pieces = 0;
  let firstTokenAt = null;
  let usage = null;
  let streamError = null;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const payload of parser.push(decoder.decode(value, { stream: true }))) {
        const event = interpretEvent(payload);
        if (event.type === "error") {
          streamError = event.message;
        } else if (event.type === "chunk") {
          if (event.usage) usage = event.usage;
          if (event.delta) {
            if (firstTokenAt === null) firstTokenAt = performance.now();
            pieces += 1;
            text += event.delta;
            setBody(view, text);
            scrollDown();
          }
        }
      }
    }
  } catch {
    if (!signal.aborted) streamError = streamError || "The connection was interrupted.";
  }
  const endedAt = performance.now();

  if (signal.aborted) {
    setBody(view, text ? `${text}\n\n[stopped]` : "Stopped.", text ? "" : "notice");
  } else if (streamError) {
    setBody(view, `${text}${text ? "\n\n" : ""}⚠ ${streamError}`, "error");
  } else if (!text) {
    setBody(view, "(empty answer)", "notice");
  }

  view.stats.textContent = formatStats({
    ttftMs: firstTokenAt === null ? null : firstTokenAt - startedAt,
    tokens: usage ? usage.completion_tokens : pieces,
    generationMs: firstTokenAt === null ? 0 : endedAt - firstTokenAt,
  });
  return text;
}

// ---------------------------------------------------------------- service status

function setPill(element, className, text) {
  element.className = `pill ${className}`.trim();
  element.textContent = text;
}

async function refreshReadiness() {
  const engine = $("engine");
  try {
    const response = await fetch("/ready");
    if (response.ok) setPill(engine, "ok", "engine ready");
    else setPill(engine, "down", "engine not ready");
  } catch {
    setPill(engine, "down", "service unreachable");
  }
}

async function refreshLoad() {
  const load = $("load");
  try {
    const response = await fetch("/metrics");
    const state = response.ok ? parseMetrics(await response.text()) : null;
    load.hidden = state === null;
    if (state) setPill(load, "plain", formatLoad(state));
  } catch {
    load.hidden = true;
  }
}

// ---------------------------------------------------------------- wiring

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = promptBox.value.trim();
  if (!text || controller) return;
  promptBox.value = "";
  autoGrow();
  sendMessage(text);
});

promptBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function autoGrow() {
  promptBox.style.height = "auto";
  promptBox.style.height = `${Math.min(promptBox.scrollHeight, 160)}px`;
}
promptBox.addEventListener("input", autoGrow);

stopButton.addEventListener("click", () => controller?.abort());

$("new-chat").addEventListener("click", () => {
  controller?.abort();
  history = [];
  log.replaceChildren();
  const hint = document.createElement("p");
  hint.className = "empty";
  hint.id = "empty";
  hint.textContent = "New conversation. Ask something.";
  log.append(hint);
  promptBox.focus();
});

refreshReadiness();
refreshLoad();
setInterval(refreshReadiness, 5000);
setInterval(refreshLoad, 2000);
promptBox.focus();
