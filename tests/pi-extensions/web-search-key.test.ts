import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import {
  createKeyResolver,
  DEFAULT_OP_REF,
  execBounded,
  FAILURE_RETRY_MS,
  makeRealRunOp,
  resolveBraveKey,
  type KeyDeps,
  type OpResult,
} from "../../pi-extensions/web-search/key.ts";

function deps(over: Partial<KeyDeps> & { op?: OpResult; files?: Record<string, string> } = {}) {
  const calls: string[] = [];
  const d: KeyDeps = {
    env: over.env ?? {},
    runOp:
      over.runOp ??
      (async (ref) => {
        calls.push(ref);
        return over.op ?? { ok: false, error: "not signed in" };
      }),
    readFile: over.readFile ?? ((p) => over.files?.[p]),
    legacyPaths: over.legacyPaths ?? ["/ext/brave.key", "/agent/brave.key"],
  };
  return { d, calls };
}

describe("resolveBraveKey order", () => {
  it("1. env wins and op is never called", async () => {
    const { d, calls } = deps({ env: { BRAVE_API_KEY: " env-key \n" }, op: { ok: true, value: "op-key" } });
    const r = await resolveBraveKey(d);
    assert.equal(r.key, "env-key");
    assert.equal(r.source, "env");
    assert.equal(calls.length, 0);
  });

  it("2. 1Password when env is empty, using the default ref and trimming", async () => {
    const { d, calls } = deps({ op: { ok: true, value: "op-key\n" }, files: { "/ext/brave.key": "file-key" } });
    const r = await resolveBraveKey(d);
    assert.equal(r.key, "op-key");
    assert.equal(r.source, "1password");
    assert.deepEqual(calls, [DEFAULT_OP_REF]);
    assert.equal(DEFAULT_OP_REF, "op://api-keys/Brave Search/credential");
  });

  it("honours BRAVE_API_KEY_OP_REF", async () => {
    const { d, calls } = deps({ env: { BRAVE_API_KEY_OP_REF: "op://v/i/f" }, op: { ok: true, value: "k" } });
    await resolveBraveKey(d);
    assert.deepEqual(calls, ["op://v/i/f"]);
  });

  it("3. legacy file only after op fails, with a deprecation note", async () => {
    const { d } = deps({ op: { ok: false, error: "timed out" }, files: { "/agent/brave.key": "file-key\n" } });
    const r = await resolveBraveKey(d);
    assert.equal(r.key, "file-key");
    assert.equal(r.source, "legacy-file");
    assert.equal(r.deprecated, true);
    assert.match(r.notes.join("\n"), /timed out/);
    assert.match(r.notes.join("\n"), /deprecated plaintext key file \/agent\/brave\.key/);
  });

  it("empty op value falls through", async () => {
    const { d } = deps({ op: { ok: true, value: "  " }, files: { "/ext/brave.key": "file-key" } });
    assert.equal((await resolveBraveKey(d)).source, "legacy-file");
  });

  it("PI_WEB_SEARCH_DISABLE_OP=1 skips op", async () => {
    const { d, calls } = deps({ env: { PI_WEB_SEARCH_DISABLE_OP: "1" }, files: { "/ext/brave.key": "k" } });
    assert.equal((await resolveBraveKey(d)).source, "legacy-file");
    assert.equal(calls.length, 0);
  });

  it("none found returns notes and no key material", async () => {
    const { d } = deps();
    const r = await resolveBraveKey(d);
    assert.equal(r.key, undefined);
    assert.equal(r.source, "none");
    assert.ok(r.notes.length > 0);
  });
});

describe("createKeyResolver", () => {
  it("caches a hit forever and dedupes concurrent calls", async () => {
    let n = 0;
    const { d } = deps({
      runOp: async () => {
        n++;
        await new Promise((r) => setTimeout(r, 20));
        return { ok: true, value: "k" };
      },
    });
    const get = createKeyResolver(d);
    const [a, b] = await Promise.all([get(), get()]);
    assert.equal(a.key, "k");
    assert.equal(b.key, "k");
    await get();
    assert.equal(n, 1);
  });

  it("caches a miss for FAILURE_RETRY_MS, then retries", async () => {
    let n = 0;
    let t = 0;
    const { d } = deps({
      runOp: async () => {
        n++;
        return { ok: false, error: "x" };
      },
    });
    const get = createKeyResolver(d, () => t);
    await get();
    await get();
    assert.equal(n, 1);
    t = FAILURE_RETRY_MS + 1;
    await get();
    assert.equal(n, 2);
  });
});

describe("real op runner guards", () => {
  it("execBounded kills a wedged process group at the deadline", async () => {
    const start = Date.now();
    const r = await execBounded("/bin/sh", ["-c", "sleep 30 & sleep 30"], { env: process.env, timeoutMs: 300 });
    assert.equal(r.timedOut, true);
    assert.ok(Date.now() - start < 5000);
  });

  it("uses an isolated HOME and the service-account token, and times out instead of hanging", async () => {
    const dir = mkdtempSync(join(tmpdir(), "fake-op-"));
    try {
      const fake = join(dir, "op");
      writeFileSync(
        fake,
        `#!/bin/sh\nif [ "$1" = "hang" ]; then sleep 30; fi\n` +
          `[ "$HOME" != "${process.env.HOME}" ] || { echo "real HOME leaked" >&2; exit 3; }\n` +
          `[ "$OP_SERVICE_ACCOUNT_TOKEN" = "tok" ] || exit 4\nprintf 'secret-%s' "$3"\n`,
      );
      chmodSync(fake, 0o755);
      const runOp = makeRealRunOp({ ...process.env, OP_BIN: fake, OP_SERVICE_ACCOUNT_TOKEN: "tok" });
      const ok = await runOp("op://a/b/c", 5000);
      assert.deepEqual(ok, { ok: true, value: "secret-op://a/b/c" });

      writeFileSync(fake, `#!/bin/sh\nsleep 30\n`);
      const start = Date.now();
      const slow = await runOp("op://a/b/c", 300);
      assert.equal(slow.ok, false);
      assert.match(slow.error!, /timed out/);
      assert.ok(Date.now() - start < 5000);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("refuses to run op without a service-account token (never an interactive sign-in)", async () => {
    if (process.platform === "darwin") return; // the keychain may legitimately hold the token here
    const runOp = makeRealRunOp({ PATH: process.env.PATH, OP_BIN: "/bin/false" });
    const r = await runOp("op://a/b/c", 1000);
    assert.equal(r.ok, false);
    assert.match(r.error!, /OP_SERVICE_ACCOUNT_TOKEN/);
  });
});
