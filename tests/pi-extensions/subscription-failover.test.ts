import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  classifyAssistant,
  classifyProviderError,
  DEFAULT_CANDIDATES,
  exhaustionSummary,
  extractReset,
  FailoverState,
  higherPriorityRecovered,
  loadConfig,
  parseCandidateList,
  resendText,
  selectNext,
  type CandidateRef,
} from "../../pi-extensions/subscription-failover/lib.ts";

const NOW = Date.parse("2026-09-28T12:00:00Z");

describe("classifyProviderError", () => {
  const cases: Array<[string, string | undefined, number | undefined, string | undefined]> = [
    ["codex usage limit", "You have hit your ChatGPT usage limit (plus plan). Try again in ~42 min.", undefined, "quota"],
    ["openai insufficient_quota", '429 {"error":{"code":"insufficient_quota"}}', undefined, "quota"],
    ["anthropic account limit", "429 This request would exceed your account's rate limit. Please try again later.", undefined, "quota"],
    [
      "anthropic subscription moved to extra usage (seen live 2026-09-28)",
      '400 {"type":"error","error":{"type":"invalid_request_error","message":"Third-party apps now draw from your extra usage, not your plan limits. Add more at claude.ai/settings/usage and keep going."}}',
      undefined,
      "quota",
    ],
    ["codex model not on plan", "Codex error: The 'gpt-5.4-mini' model is not supported when using Codex with a ChatGPT account.", undefined, "quota"],
    ["anthropic credit balance", "400 Your credit balance is too low to access the Anthropic API.", undefined, "quota"],
    ["plain 429", '429 {"type":"error","error":{"type":"rate_limit_error","message":"Error"}}', undefined, "rate_limit"],
    ["too many requests", "Too Many Requests", undefined, "rate_limit"],
    ["529 overloaded", '529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', undefined, "overloaded"],
    ["401", '401 {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}', undefined, "auth"],
    ["oauth expired", 'Authentication failed for "anthropic". Credentials may have expired. Run \'/login anthropic\'.', undefined, "auth"],
    ["403 forbidden", "403 Forbidden", undefined, "auth"],
    ["status only 429", "Provider returned error", 429, "rate_limit"],
    ["status only 401", "", 401, "auth"],
    // Ordinary task and transient errors must NOT fail over.
    ["context overflow", "400 prompt is too long: 250000 tokens > 200000 maximum", undefined, undefined],
    ["context overflow with 429 status", "context_length_exceeded", 429, undefined],
    ["500", "500 Internal server error", undefined, undefined],
    ["network", "fetch failed: ECONNRESET", undefined, undefined],
    ["bad request", '400 {"error":{"message":"Invalid value for tool_choice"}}', undefined, undefined],
    ["port number is not a status", "connect ECONNREFUSED 127.0.0.1:4290", undefined, undefined],
  ];
  for (const [name, msg, status, want] of cases) {
    it(name, () => assert.equal(classifyProviderError({ errorMessage: msg, status })?.kind, want));
  }

  it("scopes quota/auth/429 to the provider and 529 to the model", () => {
    assert.equal(classifyProviderError({ errorMessage: "usage limit" })?.scope, "provider");
    assert.equal(classifyProviderError({ errorMessage: "401" })?.scope, "provider");
    assert.equal(classifyProviderError({ errorMessage: "429" })?.scope, "provider");
    assert.equal(classifyProviderError({ errorMessage: "529 overloaded" })?.scope, "model");
    assert.equal(classifyProviderError({ errorMessage: "The 'x' model is not supported when using Codex with a ChatGPT account." })?.scope, "model");
  });

  it("only assistant error messages count", () => {
    assert.equal(classifyAssistant({ role: "assistant", stopReason: "stop", errorMessage: "429" }), undefined);
    assert.equal(classifyAssistant({ role: "assistant", stopReason: "aborted", errorMessage: "429" }), undefined);
    assert.equal(classifyAssistant({ role: "toolResult", stopReason: "error", errorMessage: "429" }), undefined);
    assert.equal(classifyAssistant(undefined), undefined);
    assert.equal(classifyAssistant({ role: "assistant", stopReason: "error", errorMessage: "429" })?.kind, "rate_limit");
  });
});

describe("extractReset", () => {
  it("reads relative minutes from Codex text", () => {
    const r = extractReset("You have hit your ChatGPT usage limit. Try again in ~42 min.", undefined, NOW);
    assert.equal(r?.at, NOW + 42 * 60_000);
    assert.match(r!.text, /~42 min/);
  });
  it("reads ISO resets_at from a body", () => {
    const r = extractReset('usage limit, resets at 2026-09-28T15:00:00Z', undefined, NOW);
    assert.equal(r?.at, Date.parse("2026-09-28T15:00:00Z"));
    assert.match(r!.text, /~3h/);
  });
  it("reads epoch resets_at", () => {
    const epoch = Math.floor((NOW + 3_600_000) / 1000);
    assert.equal(extractReset(`{"resets_at": ${epoch}}`, undefined, NOW)?.at, epoch * 1000);
  });
  it("prefers headers", () => {
    const epoch = Math.floor((NOW + 600_000) / 1000);
    const r = extractReset("Try again in 5 hours", { "Anthropic-Ratelimit-Unified-Reset": String(epoch) }, NOW);
    assert.equal(r?.at, epoch * 1000);
  });
  it("treats retry-after as seconds", () => {
    assert.equal(extractReset(undefined, { "retry-after": "30" }, NOW)?.at, NOW + 30_000);
  });
  it("keeps unparseable human text", () => {
    assert.equal(extractReset("limit reached, resets at 5pm (Europe/London).", undefined, NOW)?.text, "resets at 5pm (Europe/London)");
  });
  it("returns undefined when there is nothing", () => {
    assert.equal(extractReset("429 rate_limit_error", {}, NOW), undefined);
  });
});

describe("config", () => {
  it("defaults to Claude first, then Codex, then the second Codex account", () => {
    const { config } = loadConfig({}, undefined, "/x.json");
    assert.deepEqual(config.candidates, DEFAULT_CANDIDATES);
    assert.deepEqual(
      [...new Set(config.candidates.map((c) => c.provider))],
      ["anthropic", "openai-codex", "openai-codex-2"]
    );
    assert.equal(config.enabled, true);
  });
  it("file overrides defaults, env overrides file", () => {
    const file = JSON.stringify({ candidates: ["a/1", "b/2"], preferPrimary: false });
    let { config } = loadConfig({}, file, "/x.json");
    assert.deepEqual(config.candidates, [{ provider: "a", model: "1" }, { provider: "b", model: "2" }]);
    assert.equal(config.preferPrimary, false);
    ({ config } = loadConfig({ PI_FAILOVER_MODELS: "c/3, d/4.5" }, file, "/x.json"));
    assert.deepEqual(config.candidates, [{ provider: "c", model: "3" }, { provider: "d", model: "4.5" }]);
    assert.equal(config.source, "PI_FAILOVER_MODELS");
  });
  it("PI_FAILOVER_DISABLED=1 disables", () => {
    assert.equal(loadConfig({ PI_FAILOVER_DISABLED: "1" }, undefined, "/x").config.enabled, false);
  });
  it("bad JSON warns and keeps defaults", () => {
    const { config, warnings } = loadConfig({}, "{nope", "/x.json");
    assert.deepEqual(config.candidates, DEFAULT_CANDIDATES);
    assert.equal(warnings.length, 1);
  });
  it("rejects malformed refs and dedupes", () => {
    assert.throws(() => parseCandidateList("noslash"));
    assert.deepEqual(parseCandidateList("a/b,a/b"), [{ provider: "a", model: "b" }]);
    assert.deepEqual(parseCandidateList("openrouter/meta/llama"), [{ provider: "openrouter", model: "meta/llama" }]);
  });
});

describe("selectNext", () => {
  const list: CandidateRef[] = parseCandidateList("anthropic/opus,anthropic/sonnet,openai-codex/gpt,local/qwen");
  const all = () => true;

  it("skips the current model and returns the next in order", async () => {
    const s = new FailoverState();
    const r = await selectNext(list, s, { current: list[0], isReady: all, exists: all, now: NOW });
    assert.deepEqual(r.next, list[1]);
  });

  it("a provider-wide limit skips every model of that provider", async () => {
    const s = new FailoverState();
    s.mark(list[0], classifyProviderError({ errorMessage: "usage limit" })!, NOW);
    const r = await selectNext(list, s, { current: list[0], isReady: all, exists: all, now: NOW });
    assert.deepEqual(r.next, list[2]);
    assert.match(r.skipped.find((x) => x.candidate === "anthropic/sonnet")!.why, /quota/);
  });

  it("a model-only overload still allows another model on the same provider", async () => {
    const s = new FailoverState();
    s.mark(list[0], classifyProviderError({ errorMessage: "529 overloaded" })!, NOW);
    const r = await selectNext(list, s, { current: list[0], isReady: all, exists: all, now: NOW });
    assert.deepEqual(r.next, list[1]);
  });

  it("skips unauthenticated providers and remembers them", async () => {
    const s = new FailoverState();
    let probes = 0;
    const isReady = (c: CandidateRef) => {
      probes++;
      return c.provider !== "openai-codex";
    };
    s.mark(list[0], classifyProviderError({ errorMessage: "usage limit" })!, NOW);
    const r1 = await selectNext(list, s, { current: list[0], isReady, exists: all, now: NOW });
    assert.deepEqual(r1.next, list[3]);
    assert.ok(r1.skipped.some((x) => x.candidate === "openai-codex/gpt" && /not authenticated/.test(x.why)));
    const before = probes;
    await selectNext(list, s, { current: list[3], isReady, exists: all, now: NOW });
    assert.equal(probes - before, 0, "codex is not re-probed once marked unavailable");
  });

  it("skips models missing from the registry and treats a throwing probe as not ready", async () => {
    const s = new FailoverState();
    const r = await selectNext(list, s, {
      current: list[0],
      exists: (c) => c.model !== "sonnet",
      isReady: (c) => {
        if (c.provider === "openai-codex") throw new Error("boom");
        return true;
      },
      now: NOW,
    });
    assert.deepEqual(r.next, list[3]);
  });

  it("tries each candidate once per prompt, then reports exhaustion", async () => {
    const s = new FailoverState();
    for (const c of list) s.triedThisPrompt.add(`${c.provider}/${c.model}`);
    const r = await selectNext(list, s, { current: list[0], isReady: all, exists: all, now: NOW });
    assert.equal(r.next, undefined);
    s.newPrompt();
    assert.deepEqual((await selectNext(list, s, { current: list[0], isReady: all, exists: all, now: NOW })).next, list[1]);
  });

  it("exhaustion expires at its reset time", async () => {
    const s = new FailoverState();
    s.mark(list[0], classifyProviderError({ errorMessage: "usage limit" })!, NOW, { at: NOW + 1000, text: "soon" });
    assert.ok(s.blocking(list[1], NOW));
    assert.deepEqual(exhaustionSummary(s, NOW), ["anthropic: usage limit or quota reached, resets soon"]);
    assert.equal(s.blocking(list[1], NOW + 1001), undefined);
    assert.deepEqual(exhaustionSummary(s, NOW + 1001), []);
  });

  it("higherPriorityRecovered finds a recovered primary", () => {
    const s = new FailoverState();
    s.mark(list[0], classifyProviderError({ errorMessage: "usage limit" })!, NOW, { at: NOW + 1000, text: "soon" });
    assert.equal(higherPriorityRecovered(list, s, list[2], NOW), undefined);
    assert.deepEqual(higherPriorityRecovered(list, s, list[2], NOW + 2000), list[0]);
    assert.equal(higherPriorityRecovered(list, s, list[0], NOW + 2000), undefined);
  });
});

describe("resendText", () => {
  it("is verbatim when nothing ran", () => assert.equal(resendText("fix the bug", "a/b", "quota", false), "fix the bug"));
  it("asks to continue after tool activity", () => {
    const t = resendText("fix the bug", "a/b", "quota", true);
    assert.match(t, /stopped mid-task/);
    assert.match(t, /fix the bug$/);
  });
});
