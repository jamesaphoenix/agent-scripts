// End-to-end check of pi-extensions/subscription-failover against FAKE providers.
//
// Starts a local OpenAI-compatible HTTP server, registers two throwaway providers in a temp
// PI_CODING_AGENT_DIR (models.json), and runs real `pi -p --no-session -e <extension>`.
// No real subscription is used. Skips when `pi` is not installed.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { after, before, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const EXT = resolve(here, "../../pi-extensions/subscription-failover");
const PI = process.env.PI_BIN || "pi";
const hasPi = spawnSync(PI, ["--version"], { stdio: "ignore" }).status === 0;

// provider -> behaviour for the current scenario
let behaviour = {};
const hits = [];

function sseAnswer(res, model, text) {
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
  const chunk = (delta, finish) =>
    `data: ${JSON.stringify({
      id: "chatcmpl-fake",
      object: "chat.completion.chunk",
      created: 0,
      model,
      choices: [{ index: 0, delta, finish_reason: finish }],
    })}\n\n`;
  res.write(chunk({ role: "assistant", content: text }, null));
  res.write(chunk({}, "stop"));
  res.write(
    `data: ${JSON.stringify({
      id: "chatcmpl-fake",
      object: "chat.completion.chunk",
      created: 0,
      model,
      choices: [],
      usage: { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 },
    })}\n\n`,
  );
  res.end("data: [DONE]\n\n");
}

const server = createServer((req, res) => {
  let body = "";
  req.on("data", (d) => (body += d));
  req.on("end", () => {
    const provider = req.url.split("/")[1];
    let parsed = {};
    try {
      parsed = JSON.parse(body);
    } catch {}
    const lastUser = [...(parsed.messages ?? [])].reverse().find((m) => m.role === "user");
    const lastUserText = typeof lastUser?.content === "string" ? lastUser.content : JSON.stringify(lastUser?.content ?? "");
    hits.push({ provider, lastUserText });
    const b = behaviour[provider] ?? { answer: `default answer from ${provider}` };
    if (b.status) {
      res.writeHead(b.status, { "content-type": "application/json", ...(b.headers ?? {}) });
      res.end(JSON.stringify(b.body));
      return;
    }
    sseAnswer(res, parsed.model, b.answer);
  });
});

let base;
let agentDir;

function runPi(prompt, { models = "fake-a/model-a,fake-b/model-b", timeoutMs = 60_000 } = {}) {
  return new Promise((resolvePromise) => {
    const started = Date.now();
    const child = spawn(
      PI,
      ["-p", "--no-session", "-ne", "-ns", "-nc", "-np", "--offline", "-e", EXT, "--provider", "fake-a", "--model", "model-a", prompt],
      {
        cwd: agentDir,
        env: { ...process.env, PI_CODING_AGENT_DIR: agentDir, PI_FAILOVER_MODELS: models, PI_OFFLINE: "1" },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolvePromise({ code, signal, stdout, stderr, ms: Date.now() - started });
    });
  });
}

const model = (id) => ({
  id,
  name: id,
  reasoning: false,
  input: ["text"],
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  contextWindow: 32000,
  maxTokens: 1024,
});

describe("subscription-failover e2e (fake providers)", { skip: !hasPi && "pi is not installed" }, () => {
  before(async () => {
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    base = `http://127.0.0.1:${server.address().port}`;
    agentDir = mkdtempSync(join(tmpdir(), "pi-failover-e2e-"));
    const provider = (name, id) => ({
      baseUrl: `${base}/${name}/v1`,
      api: "openai-completions",
      apiKey: "local",
      models: [model(id)],
    });
    writeFileSync(
      join(agentDir, "models.json"),
      JSON.stringify({ providers: { "fake-a": provider("fake-a", "model-a"), "fake-b": provider("fake-b", "model-b"), "fake-c": provider("fake-c", "model-c") } }, null, 2),
    );
    // Fast backoff so pi's own auto-retry path is exercised quickly.
    writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ retry: { enabled: true, maxRetries: 3, baseDelayMs: 50, provider: { maxRetries: 0 } } }));
  });

  after(() => {
    server.close();
    if (agentDir) rmSync(agentDir, { recursive: true, force: true });
  });

  it("usage-limit 429 on the first provider switches and the retry succeeds on the second", async () => {
    hits.length = 0;
    behaviour = {
      "fake-a": {
        status: 429,
        body: { error: { message: "You have hit your usage limit. Try again in 37 min.", type: "usage_limit_reached", code: "usage_limit_reached" } },
      },
      "fake-b": { answer: "FAILOVER-OK from fake-b" },
    };
    const r = await runPi("Say hello");
    console.log(`--- scenario 1 (429 usage limit) exit=${r.code} ${r.ms}ms\nstdout: ${r.stdout.trim()}\nstderr: ${r.stderr.trim()}\nhits: ${JSON.stringify(hits)}`);
    assert.equal(r.code, 0);
    assert.match(r.stdout, /FAILOVER-OK from fake-b/);
    assert.match(r.stderr, /fake-a\/model-a: usage limit or quota reached \(resets ~37 min/);
    assert.match(r.stderr, /Switching to fake-b\/model-b/);
    assert.deepEqual(hits.map((h) => h.provider), ["fake-a", "fake-b"]);
    assert.equal(hits[1].lastUserText.includes("Say hello"), true);
  });

  it("non-retryable quota error: the extension re-sends the prompt on the next candidate", async () => {
    hits.length = 0;
    behaviour = {
      "fake-a": { status: 402, body: { error: { message: "insufficient_quota: You exceeded your current quota", type: "insufficient_quota" } } },
      "fake-b": { answer: "RESENT-OK from fake-b" },
    };
    const r = await runPi("Say hello again");
    console.log(`--- scenario 2 (insufficient_quota, re-send) exit=${r.code} ${r.ms}ms\nstdout: ${r.stdout.trim()}\nstderr: ${r.stderr.trim()}\nhits: ${JSON.stringify(hits)}`);
    assert.equal(r.code, 0);
    assert.match(r.stdout, /RESENT-OK from fake-b/);
    assert.match(r.stderr, /Recovered on fake-b\/model-b/);
    assert.deepEqual(hits.map((h) => h.provider), ["fake-a", "fake-b"]);
    assert.equal(hits[1].lastUserText.includes("Say hello again"), true);
  });

  it("auth failure (401) skips to the next candidate", async () => {
    hits.length = 0;
    behaviour = {
      "fake-a": { status: 401, body: { error: { message: "Invalid bearer token: token has expired", type: "authentication_error" } } },
      "fake-b": { answer: "AUTH-FAILOVER-OK" },
    };
    const r = await runPi("hi");
    console.log(`--- scenario 3 (401) exit=${r.code} ${r.ms}ms\nstdout: ${r.stdout.trim()}\nstderr: ${r.stderr.trim()}`);
    assert.equal(r.code, 0);
    assert.match(r.stdout, /AUTH-FAILOVER-OK/);
  });

  it("an ordinary 400 task error does NOT fail over", async () => {
    hits.length = 0;
    behaviour = {
      "fake-a": { status: 400, body: { error: { message: "Invalid value for 'tool_choice'", type: "invalid_request_error" } } },
      "fake-b": { answer: "SHOULD-NOT-BE-CALLED" },
    };
    const r = await runPi("hi");
    console.log(`--- scenario 4 (400, no failover) exit=${r.code} ${r.ms}ms\nstderr: ${r.stderr.trim()}`);
    assert.equal(r.code, 1);
    assert.doesNotMatch(r.stdout, /SHOULD-NOT-BE-CALLED/);
    assert.deepEqual(hits.map((h) => h.provider), ["fake-a"]);
  });

  it("everything exhausted: stops, names providers and reset times, and does not hang", async () => {
    hits.length = 0;
    const resetAt = Math.floor(Date.now() / 1000) + 2 * 3600;
    behaviour = {
      "fake-a": { status: 402, body: { error: { message: "insufficient_quota", type: "insufficient_quota" } } },
      "fake-b": { status: 402, body: { error: { message: `usage limit reached, resets_at: ${resetAt}` } } },
      "fake-c": { status: 402, body: { error: { message: "insufficient_quota" } } },
    };
    const r = await runPi("hi", { models: "fake-a/model-a,fake-b/model-b,fake-c/model-c", timeoutMs: 45_000 });
    console.log(`--- scenario 5 (all exhausted) exit=${r.code} signal=${r.signal} ${r.ms}ms\nstderr: ${r.stderr.trim()}\nhits: ${JSON.stringify(hits.map((h) => h.provider))}`);
    assert.equal(r.signal, null, "pi must exit on its own");
    assert.equal(r.code, 1);
    assert.match(r.stderr, /All failover candidates are exhausted/);
    assert.match(r.stderr, /fake-b: usage limit or quota reached, resets ~2h/);
    assert.deepEqual(hits.map((h) => h.provider), ["fake-a", "fake-b", "fake-c"], "each candidate tried exactly once");
  });

  it("/failover status works in print mode", async () => {
    const r = await runPi("/failover status");
    // pi's print-mode output guard routes extension stdout writes to stderr.
    const out = r.stdout + r.stderr;
    assert.equal(r.code, 0);
    assert.match(out, /subscription-failover: enabled/);
    assert.match(out, /\* fake-a\/model-a {2}ready/);
  });
});
