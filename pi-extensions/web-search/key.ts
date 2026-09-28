/**
 * Brave Search API key resolution for the pi web-search extension.
 *
 * Order:
 *   1. BRAVE_API_KEY environment variable
 *   2. `op read <ref>` through the 1Password service account (bounded, isolated HOME)
 *   3. a legacy plaintext `brave.key` file (deprecated, warns once)
 *
 * Resolution is lazy (first tool call) and cached in memory, so pi startup never waits on `op`.
 * This file has no pi imports so it can be unit-tested with plain `node --test`.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

export const DEFAULT_OP_REF = "op://api-keys/Brave Search/credential";
export const OP_KEYCHAIN_SERVICE = "op_service_account_token_cli_automation";
export const DEFAULT_OP_TIMEOUT_MS = 10_000;
/** After a failed lookup, wait this long before trying `op` again (avoids a 10s stall per call). */
export const FAILURE_RETRY_MS = 60_000;

export type KeySource = "env" | "1password" | "legacy-file" | "none";

export interface KeyResolution {
  key?: string;
  source: KeySource;
  /** Human-readable notes: why a step was skipped or failed, deprecation warnings. Never contains key material. */
  notes: string[];
  deprecated?: boolean;
}

export interface OpResult {
  ok: boolean;
  value?: string;
  error?: string;
}

export interface KeyDeps {
  env: Record<string, string | undefined>;
  runOp: (ref: string, timeoutMs: number) => Promise<OpResult>;
  readFile: (path: string) => string | undefined;
  legacyPaths: string[];
}

export function opRefFrom(env: Record<string, string | undefined>): string {
  return env.BRAVE_API_KEY_OP_REF?.trim() || DEFAULT_OP_REF;
}

export function opTimeoutFrom(env: Record<string, string | undefined>): number {
  const raw = Number(env.PI_WEB_SEARCH_OP_TIMEOUT_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_OP_TIMEOUT_MS;
}

export async function resolveBraveKey(deps: KeyDeps): Promise<KeyResolution> {
  const notes: string[] = [];

  const fromEnv = deps.env.BRAVE_API_KEY?.trim();
  if (fromEnv) return { key: fromEnv, source: "env", notes };

  if (deps.env.PI_WEB_SEARCH_DISABLE_OP === "1") {
    notes.push("1Password lookup disabled by PI_WEB_SEARCH_DISABLE_OP=1");
  } else {
    const ref = opRefFrom(deps.env);
    const result = await deps.runOp(ref, opTimeoutFrom(deps.env));
    const value = result.value?.trim();
    if (result.ok && value) return { key: value, source: "1password", notes };
    notes.push(`1Password lookup of ${ref} failed: ${result.error ?? "empty value"}`);
  }

  for (const path of deps.legacyPaths) {
    const value = deps.readFile(path)?.trim();
    if (value) {
      notes.push(
        `Using the deprecated plaintext key file ${path}. Store the key in 1Password ` +
          `(${opRefFrom(deps.env)}) or set BRAVE_API_KEY, then delete the file.`,
      );
      return { key: value, source: "legacy-file", notes, deprecated: true };
    }
  }

  return { source: "none", notes };
}

/** Memoising wrapper: caches a found key forever, a miss for FAILURE_RETRY_MS, and dedupes concurrent calls. */
export function createKeyResolver(deps: KeyDeps, now: () => number = Date.now) {
  let cached: KeyResolution | undefined;
  let missAt = 0;
  let inFlight: Promise<KeyResolution> | undefined;

  return async function getKey(): Promise<KeyResolution> {
    if (cached?.key) return cached;
    if (cached && now() - missAt < FAILURE_RETRY_MS) return cached;
    if (!inFlight) {
      inFlight = resolveBraveKey(deps)
        .then((res) => {
          cached = res;
          if (!res.key) missAt = now();
          return res;
        })
        .finally(() => {
          inFlight = undefined;
        });
    }
    return inFlight;
  };
}

// ---------------------------------------------------------------------------
// Real dependencies
// ---------------------------------------------------------------------------

interface ExecResult {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  spawnError?: string;
}

/**
 * Run a command in its own process group with a hard deadline. On timeout the whole group is
 * SIGKILLed: `op` is a Go binary and ignores softer signals in some wedge states.
 */
export function execBounded(
  cmd: string,
  args: string[],
  opts: { env?: Record<string, string | undefined>; timeoutMs: number },
): Promise<ExecResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const finish = (r: ExecResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(r);
    };
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(cmd, args, {
        env: opts.env as NodeJS.ProcessEnv,
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (err) {
      resolve({ code: null, stdout, stderr, timedOut, spawnError: String(err) });
      return;
    }
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        if (child.pid) process.kill(-child.pid, "SIGKILL");
      } catch {
        /* already gone */
      }
      finish({ code: null, stdout, stderr, timedOut });
    }, opts.timeoutMs);
    timer.unref?.();
    child.stdout?.on("data", (d) => (stdout += String(d)));
    child.stderr?.on("data", (d) => (stderr += String(d)));
    child.on("error", (err) => finish({ code: null, stdout, stderr, timedOut, spawnError: err.message }));
    child.on("close", (code) => finish({ code, stdout, stderr, timedOut }));
  });
}

function findOpBinary(env: Record<string, string | undefined>): string {
  if (env.OP_BIN) return env.OP_BIN;
  for (const candidate of ["/opt/homebrew/bin/op", "/usr/local/bin/op"]) {
    if (existsSync(candidate)) return candidate;
  }
  return "op";
}

async function serviceAccountToken(env: Record<string, string | undefined>): Promise<string | undefined> {
  if (env.OP_SERVICE_ACCOUNT_TOKEN) return env.OP_SERVICE_ACCOUNT_TOKEN;
  if (process.platform !== "darwin") return undefined;
  const r = await execBounded("/usr/bin/security", ["find-generic-password", "-s", OP_KEYCHAIN_SERVICE, "-w"], {
    env: process.env,
    timeoutMs: 5_000,
  });
  const token = r.code === 0 ? r.stdout.trim() : "";
  return token || undefined;
}

/**
 * `op read` using the cli-automation service account, never an interactive or biometric session.
 * HOME points at an empty temp dir so op cannot touch the 1Password app container, which wedges
 * under TCC in launchd sessions (see agent-scripts/lib/op-cli.sh).
 */
export function makeRealRunOp(env: Record<string, string | undefined> = process.env) {
  return async function runOp(ref: string, timeoutMs: number): Promise<OpResult> {
    const token = await serviceAccountToken(env);
    if (!token) {
      return { ok: false, error: `no OP_SERVICE_ACCOUNT_TOKEN in env or keychain service ${OP_KEYCHAIN_SERVICE}` };
    }
    const isolatedHome = mkdtempSync(join(tmpdir(), "pi-web-search-op-"));
    try {
      const childEnv: Record<string, string | undefined> = {
        ...env,
        HOME: isolatedHome,
        OP_SERVICE_ACCOUNT_TOKEN: token,
        OP_BIOMETRIC_UNLOCK_ENABLED: "false",
      };
      const r = await execBounded(findOpBinary(env), ["read", "--no-newline", ref], { env: childEnv, timeoutMs });
      if (r.timedOut) return { ok: false, error: `op read timed out after ${timeoutMs}ms` };
      if (r.spawnError) return { ok: false, error: `could not run op: ${r.spawnError}` };
      if (r.code !== 0) return { ok: false, error: `op exited ${r.code}: ${r.stderr.trim().slice(0, 300)}` };
      return { ok: true, value: r.stdout };
    } finally {
      rmSync(isolatedHome, { recursive: true, force: true });
    }
  };
}

export function readFileIfPresent(path: string): string | undefined {
  try {
    return existsSync(path) ? readFileSync(path, "utf8") : undefined;
  } catch {
    return undefined;
  }
}

/** Legacy key locations: next to the extension, then the pi agent dir (where install.sh parks it). */
export function defaultLegacyPaths(extensionDir: string, agentDir: string = join(homedir(), ".pi", "agent")): string[] {
  return [join(extensionDir, "brave.key"), join(agentDir, "brave.key")];
}
