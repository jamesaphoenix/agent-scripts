import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import {
  extraAccountCount,
  extraAccountIds,
  locateCodexProviderModule,
  makeCodexAccountProvider,
} from "../../pi-extensions/codex-accounts/index.ts";

type Seen = { method: string; provider: unknown; args: unknown[] }[];

function fakeBase(seen: Seen) {
  const model = (id: string) => ({ id, provider: "openai-codex", api: "openai-codex-responses" });
  return {
    id: "openai-codex",
    name: "OpenAI Codex",
    baseUrl: "https://chatgpt.com/backend-api",
    headers: { "x-test": "1" },
    auth: { oauth: { name: "OpenAI (ChatGPT Plus/Pro)" } },
    getModels: () => [model("gpt-6-astra"), model("gpt-5.5")],
    filterModels: (models: readonly { provider: string }[]) => {
      seen.push({ method: "filterModels", provider: models[0]?.provider, args: [] });
      return models;
    },
    stream: (m: { provider: string }, ...args: unknown[]) => {
      seen.push({ method: "stream", provider: m.provider, args });
      return "stream-result";
    },
    streamSimple: (m: { provider: string }, ...args: unknown[]) => {
      seen.push({ method: "streamSimple", provider: m.provider, args });
      return "simple-result";
    },
  };
}

describe("codex-accounts config", () => {
  it("defaults to one extra account and caps the count", () => {
    assert.equal(extraAccountCount(undefined), 1);
    assert.equal(extraAccountCount(""), 1);
    assert.equal(extraAccountCount("3"), 3);
    assert.equal(extraAccountCount("0"), 0);
    assert.equal(extraAccountCount("99"), 5);
    assert.equal(extraAccountCount("nope"), 1);
    assert.equal(extraAccountCount("-2"), 1);
  });

  it("numbers extra accounts from 2", () => {
    assert.deepEqual(extraAccountIds(2), ["openai-codex-2", "openai-codex-3"]);
    assert.deepEqual(extraAccountIds(0), []);
  });
});

describe("makeCodexAccountProvider", () => {
  it("exposes its own id and relabels models while sharing auth and endpoint", () => {
    const seen: Seen = [];
    const base = fakeBase(seen);
    const account = makeCodexAccountProvider("openai-codex-2", base as never);

    assert.equal(account.id, "openai-codex-2");
    assert.equal(account.name, "OpenAI Codex (account 2)");
    assert.equal(account.auth, base.auth);
    assert.equal(account.baseUrl, base.baseUrl);
    assert.deepEqual(
      account.getModels().map((m) => `${m.provider}/${String(m.id)}`),
      ["openai-codex-2/gpt-6-astra", "openai-codex-2/gpt-5.5"]
    );
  });

  it("hands models back to the base provider unchanged when streaming", () => {
    const seen: Seen = [];
    const account = makeCodexAccountProvider("openai-codex-2", fakeBase(seen) as never);
    const [model] = account.getModels();

    assert.equal(account.stream(model!, "ctx", { apiKey: "k" }), "stream-result");
    assert.equal(account.streamSimple(model!, "ctx"), "simple-result");
    assert.deepEqual(
      seen.map((s) => `${s.method}:${String(s.provider)}`),
      ["stream:openai-codex", "streamSimple:openai-codex"]
    );
    assert.deepEqual(seen[0]!.args, ["ctx", { apiKey: "k" }]);
    assert.equal(model!.provider, "openai-codex-2", "the account model itself is not mutated");
  });

  it("filters with base-labelled models and returns account-labelled ones", () => {
    const seen: Seen = [];
    const account = makeCodexAccountProvider("openai-codex-3", fakeBase(seen) as never);
    const filtered = account.filterModels!(account.getModels(), undefined);

    assert.equal(seen[0]!.provider, "openai-codex");
    assert.ok(filtered.every((m) => m.provider === "openai-codex-3"));
  });
});

describe("locateCodexProviderModule", () => {
  it("finds pi-ai's Codex provider next to the pi entrypoint", () => {
    // realpath: macOS tmpdir is under /var, a symlink to /private/var.
    const root = realpathSync(mkdtempSync(join(tmpdir(), "codex-accounts-")));
    try {
      const pkg = join(root, "pi-coding-agent");
      const entry = join(pkg, "dist", "bundle", "cli.js");
      const provider = join(pkg, "node_modules", "@earendil-works", "pi-ai", "dist", "providers", "openai-codex.js");
      mkdirSync(join(pkg, "dist", "bundle"), { recursive: true });
      mkdirSync(join(provider, ".."), { recursive: true });
      writeFileSync(entry, "");
      writeFileSync(provider, "");

      assert.equal(locateCodexProviderModule(entry, undefined), provider);
      assert.equal(locateCodexProviderModule(join(root, "missing.js"), undefined), undefined);
      assert.equal(locateCodexProviderModule(entry, join(root, "nope.js")), undefined);
      assert.equal(locateCodexProviderModule(undefined, provider), provider);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
