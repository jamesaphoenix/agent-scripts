import { test } from "node:test";
import assert from "node:assert/strict";
import { oauthEnvironment, researchSchema, recordUsage, researchThread, researchTowns, researchSourceSha, researchScope } from "./agentic.mjs";

test("campaign-specific subject scope overrides broad legacy SDK research", () => {
  const sha256 = "a".repeat(64);
  const result = researchScope({ strategy: "expansion" }, "/unused", () => ({ status: 0,
    stdout: JSON.stringify({ prompt: "English, maths, verbal reasoning, non-verbal reasoning. Exclude music-only teachers.", sha256 }) }));
  assert.match(result.prompt, /Exclude music-only/);
  assert.equal(result.sha256, sha256);
  assert.match(researchScope({ strategy: "expansion" }, "/unused", () => ({ status: 0,
    stdout: '{"prompt":null,"sha256":null}' })).prompt, /all academic subjects/);
});

test("invalid subject policy cannot silently fall back to a broad SDK search", () => {
  for (const result of [{ status: 1 }, { status: 0, stdout: "bad JSON" },
    { status: 0, stdout: '{"prompt":"unverified broad search","sha256":null}' }]) {
    assert.throws(() => researchScope({}, "/unused", () => result));
  }
});

test("SDK source attribution uses the loaded source checkout", () => {
  const sha = "a".repeat(40);
  assert.equal(researchSourceSha(() => ({ status: 0, stdout: sha + "\n" })), sha);
});

test("failed or malformed Git attribution cannot stop research or expose stderr", () => {
  for (const result of [{ status: 1, stdout: "", stderr: "private diagnostic" },
                         { status: 0, stdout: "unexpected output" }, { status: null }]) {
    assert.equal(researchSourceSha(() => result), "unknown");
  }
  assert.equal(researchSourceSha(() => { throw new Error("private diagnostic"); }), "unknown");
});

test("isolated turns do not reload a growing previous conversation", () => {
  const calls = [];
  const codex = { startThread: () => calls.push("new"), resumeThread: (id) => calls.push(id) };
  researchThread(codex, {}, { threadId: "previous" }, { codexThreadMode: "isolated" });
  researchThread(codex, {}, { threadId: "previous" }, {});
  assert.deepEqual(calls, ["new", "previous"]);
  assert.throws(() => researchThread(codex, {}, {}, { codexThreadMode: "unknown" }));
});

test("town rotation wraps without silently shortening the research turn", () => {
  assert.deepEqual(researchTowns({ towns: [0, 1, 2, 3, 4], sdkTownOffset: 3 }, 0), [3, 4, 0, 1]);
});

test("SDK children cannot inherit API keys or provider credentials", () => {
  const environment = oauthEnvironment({ HOME: "/user", PATH: "/bin", OPENAI_API_KEY: "secret", CODEX_API_KEY: "secret", ANTHROPIC_API_KEY: "secret", OP_SERVICE_ACCOUNT_TOKEN: "secret", DATAFORSEO_PASSWORD: "secret" });
  assert.deepEqual(environment, { HOME: "/user", PATH: "/bin" });
});

test("every research lead requires source attribution and qualification notes", () => {
  const lead = researchSchema.properties.leads.items;
  assert.ok(lead.required.includes("sourceUrls"));
  assert.ok(lead.required.includes("notes"));
  assert.equal(lead.properties.sourceUrls.minItems, 1);
  assert.equal(lead.additionalProperties, false);
});

test("completed provider usage remains available when structured-result parsing fails", () => {
  const previous = { totalInputTokens: 100, totalCachedInputTokens: 50, totalOutputTokens: 10, providerCompletedTurns: 1 };
  const status = {};
  recordUsage(status, previous, { input_tokens: 200, cached_input_tokens: 150, output_tokens: 20 });
  assert.equal(status.totalInputTokens, 300);
  assert.equal(status.totalCachedInputTokens, 200);
  assert.equal(status.totalOutputTokens, 30);
  assert.equal(status.providerCompletedTurns, 2);
  assert.throws(() => JSON.parse("invalid structured result"));
  assert.equal(status.totalInputTokens, 300);
  assert.equal(status.completedTurns, undefined);
  assert.equal(previous.totalInputTokens, 100);
});
