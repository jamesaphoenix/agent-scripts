/**
 * Pure logic for the subscription-failover pi extension: error classification, reset-time
 * extraction, config parsing and candidate selection. No pi imports, so it runs under
 * plain `node --test`.
 */

export type FailoverKind = "quota" | "rate_limit" | "overloaded" | "auth";

export interface Classification {
  kind: FailoverKind;
  /** "provider": every model of this provider is unusable (subscription-wide). "model": only this model. */
  scope: "provider" | "model";
  /** Short human-readable reason. */
  reason: string;
}

export interface ProviderSignal {
  errorMessage?: string;
  /** Last HTTP status seen for this request via after_provider_response, if any. */
  status?: number;
}

// Order matters: the first matching rule wins.
const RULES: Array<{ kind: FailoverKind; scope: "provider" | "model"; reason: string; re: RegExp }> = [
  {
    kind: "quota",
    scope: "provider",
    reason: "usage limit or quota reached",
    re: /usage[_ ]limit|extra usage|plan limits?\b|settings\/usage|usage_not_included|insufficient_quota|quota[_ ]?(exceeded|exhausted)|exceeded (your|the) (current )?quota|out of (budget|credits)|credit balance is too low|billing|reached your (usage|weekly|monthly|daily|5-hour) limit|FreeUsageLimitError|GoUsageLimitError|would exceed your account'?s rate limit/i,
  },
  {
    kind: "auth",
    scope: "provider",
    reason: "authentication failed or credentials expired",
    re: /\b(401|403)\b|unauthori[sz]ed|authentication[_ ](error|failed)|permission[_ ]error|forbidden|invalid[_ ](x-)?api[_ -]?key|invalid bearer|invalid_grant|(token|credentials?) (has |have |may have )?(expired|been revoked)|oauth token|refresh token|not logged in|please (log|sign) ?in|\/login\b|No API key/i,
  },
  {
    kind: "quota",
    scope: "model",
    reason: "model not available on this subscription",
    re: /model is not supported when using|not supported (with|on|for) (your|this) (plan|account|subscription)|model_not_found|do(es)? not have access to (the )?model/i,
  },
  {
    kind: "overloaded",
    scope: "model",
    reason: "provider overloaded",
    re: /\b529\b|overloaded/i,
  },
  {
    kind: "rate_limit",
    scope: "provider",
    reason: "rate limited",
    re: /\b429\b|rate[_ -]?limit|too many requests/i,
  },
];

// Errors that look like a match but belong to the task, not the subscription.
const NOT_FAILOVER = /context[_ ](length|window)|prompt is too long|maximum context|too many tokens|request too large|\b413\b/i;

export function classifyProviderError(signal: ProviderSignal): Classification | undefined {
  const text = signal.errorMessage ?? "";
  if (text && NOT_FAILOVER.test(text)) return undefined;
  for (const rule of RULES) {
    if (text && rule.re.test(text)) return { kind: rule.kind, scope: rule.scope, reason: rule.reason };
  }
  switch (signal.status) {
    case 401:
    case 403:
      return { kind: "auth", scope: "provider", reason: `HTTP ${signal.status}` };
    case 429:
      return { kind: "rate_limit", scope: "provider", reason: "HTTP 429" };
    case 529:
      return { kind: "overloaded", scope: "model", reason: "HTTP 529" };
    default:
      return undefined;
  }
}

export interface AssistantLike {
  role: string;
  stopReason?: string;
  errorMessage?: string;
}

/** Only provider errors on an assistant message can trigger failover; aborts and normal stops never do. */
export function classifyAssistant(msg: AssistantLike | undefined, status?: number): Classification | undefined {
  if (!msg || msg.role !== "assistant" || msg.stopReason !== "error") return undefined;
  return classifyProviderError({ errorMessage: msg.errorMessage, status });
}

// ---------------------------------------------------------------------------
// Reset time extraction
// ---------------------------------------------------------------------------

export interface ResetInfo {
  /** Epoch ms when the limit is expected to lift, if known. */
  at?: number;
  /** Text to show the user. */
  text: string;
}

const UNIT_MS: Record<string, number> = {
  s: 1000, sec: 1000, secs: 1000, second: 1000, seconds: 1000,
  m: 60_000, min: 60_000, mins: 60_000, minute: 60_000, minutes: 60_000,
  h: 3_600_000, hr: 3_600_000, hrs: 3_600_000, hour: 3_600_000, hours: 3_600_000,
  d: 86_400_000, day: 86_400_000, days: 86_400_000,
};

function parseEpochOrDate(raw: string, now: number): number | undefined {
  const trimmed = raw.trim();
  if (/^\d{9,13}(\.\d+)?$/.test(trimmed)) {
    const n = Number(trimmed);
    return n < 1e12 ? n * 1000 : n;
  }
  if (/^\d+(\.\d+)?$/.test(trimmed)) return now + Number(trimmed) * 1000; // retry-after seconds
  const t = Date.parse(trimmed);
  return Number.isNaN(t) ? undefined : t;
}

export function formatReset(at: number, now: number): string {
  const mins = Math.max(0, Math.round((at - now) / 60_000));
  const rel = mins >= 120 ? `~${Math.round(mins / 60)}h` : `~${mins} min`;
  return `${rel} (at ${new Date(Math.round(at / 1000) * 1000).toISOString().replace(".000Z", "Z")})`;
}

/**
 * Pull a reset time out of an error message or response headers. Returns undefined when none is given.
 * Headers are checked first because they are machine-readable.
 */
export function extractReset(
  errorMessage: string | undefined,
  headers: Record<string, string> | undefined,
  now: number = Date.now(),
): ResetInfo | undefined {
  if (headers) {
    const lower: Record<string, string> = {};
    for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = String(v);
    for (const name of [
      "anthropic-ratelimit-unified-reset",
      "x-codex-primary-reset-at",
      "x-ratelimit-reset-requests",
      "x-ratelimit-reset",
      "retry-after",
    ]) {
      const v = lower[name];
      if (!v) continue;
      const at = parseEpochOrDate(v, now);
      if (at !== undefined && at > now - 60_000) return { at, text: formatReset(at, now) };
    }
  }
  const text = errorMessage ?? "";
  // "resets_at": 1790000000 / resets at 2026-09-29T00:00:00Z / reset at 5pm
  const abs = text.match(/resets?[_ ]?(?:at|on)["']?\s*[:=]?\s*["']?([0-9]{9,13}|\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)/i);
  if (abs) {
    const at = parseEpochOrDate(abs[1], now);
    if (at !== undefined) return { at, text: formatReset(at, now) };
  }
  // "try again in ~42 min", "resets in 3 hours", "retry after 30 seconds"
  const rel = text.match(/(?:try again|resets?|retry|available again)\s+(?:in|after)\s+~?\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b/i);
  if (rel) {
    const ms = Number(rel[1]) * (UNIT_MS[rel[2].toLowerCase()] ?? 60_000);
    const at = now + ms;
    return { at, text: formatReset(at, now) };
  }
  // Human text we cannot parse, e.g. "resets at 5pm (Europe/London)".
  const human = text.match(/(resets?\s+(?:at|on|in)\s+[^.;\n]{1,60})/i);
  if (human) return { text: human[1].trim() };
  return undefined;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface CandidateRef {
  provider: string;
  model: string;
}

export interface FailoverConfig {
  enabled: boolean;
  candidates: CandidateRef[];
  /** When a higher-priority candidate's reset time has passed, switch back to it on the next user prompt. */
  preferPrimary: boolean;
  /** How long print mode waits for a re-sent prompt to start before giving up on that candidate. */
  startTimeoutMs: number;
  /** Bound on the readiness probe (credential refresh) per candidate. */
  readyTimeoutMs: number;
  source: string;
}

/** Strong coding models first on the Claude subscription, then the ChatGPT (Codex) subscription. */
export const DEFAULT_CANDIDATES: CandidateRef[] = [
  { provider: "anthropic", model: "claude-opus-5-5" },
  { provider: "anthropic", model: "claude-sonnet-5" },
  { provider: "openai-codex", model: "gpt-6-astra" },
  { provider: "openai-codex", model: "gpt-5.5" },
];

export function parseCandidateList(raw: string | string[]): CandidateRef[] {
  const items = Array.isArray(raw) ? raw : raw.split(/[,\n]/);
  const out: CandidateRef[] = [];
  for (const item of items) {
    const s = String(item).trim();
    if (!s) continue;
    const slash = s.indexOf("/");
    if (slash <= 0 || slash === s.length - 1) throw new Error(`invalid candidate "${s}" (expected provider/model)`);
    const ref = { provider: s.slice(0, slash), model: s.slice(slash + 1) };
    if (!out.some((c) => c.provider === ref.provider && c.model === ref.model)) out.push(ref);
  }
  return out;
}

export function loadConfig(
  env: Record<string, string | undefined>,
  fileText: string | undefined,
  filePath: string,
): { config: FailoverConfig; warnings: string[] } {
  const warnings: string[] = [];
  const config: FailoverConfig = {
    enabled: true,
    candidates: DEFAULT_CANDIDATES,
    preferPrimary: true,
    startTimeoutMs: 30_000,
    readyTimeoutMs: 20_000,
    source: "defaults",
  };
  if (fileText !== undefined && fileText.trim()) {
    try {
      const data = JSON.parse(fileText) as Record<string, unknown>;
      if (typeof data.enabled === "boolean") config.enabled = data.enabled;
      if (typeof data.preferPrimary === "boolean") config.preferPrimary = data.preferPrimary;
      if (typeof data.startTimeoutMs === "number" && data.startTimeoutMs > 0) config.startTimeoutMs = data.startTimeoutMs;
      if (typeof data.readyTimeoutMs === "number" && data.readyTimeoutMs > 0) config.readyTimeoutMs = data.readyTimeoutMs;
      if (Array.isArray(data.candidates)) {
        const list = parseCandidateList(data.candidates as string[]);
        if (list.length) {
          config.candidates = list;
          config.source = filePath;
        }
      }
    } catch (err) {
      warnings.push(`ignoring ${filePath}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  if (env.PI_FAILOVER_MODELS?.trim()) {
    try {
      const list = parseCandidateList(env.PI_FAILOVER_MODELS);
      if (list.length) {
        config.candidates = list;
        config.source = "PI_FAILOVER_MODELS";
      }
    } catch (err) {
      warnings.push(`ignoring PI_FAILOVER_MODELS: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  if (env.PI_FAILOVER_DISABLED === "1") config.enabled = false;
  return { config, warnings };
}

// ---------------------------------------------------------------------------
// Exhaustion state and candidate selection
// ---------------------------------------------------------------------------

export interface Exhaustion {
  kind: FailoverKind | "unavailable";
  reason: string;
  since: number;
  reset?: ResetInfo;
}

export const keyOf = (c: CandidateRef) => `${c.provider}/${c.model}`;

export class FailoverState {
  /** provider -> exhaustion (subscription-wide problems). */
  providers = new Map<string, Exhaustion>();
  /** provider/model -> exhaustion (model-only problems such as 529 overloaded). */
  models = new Map<string, Exhaustion>();
  /** Candidates already used for the current user prompt ("once per candidate"). */
  triedThisPrompt = new Set<string>();

  mark(c: CandidateRef, cls: Classification | { kind: "unavailable"; scope: "provider" | "model"; reason: string }, now: number, reset?: ResetInfo) {
    const entry: Exhaustion = { kind: cls.kind, reason: cls.reason, since: now, reset };
    if (cls.scope === "provider") this.providers.set(c.provider, entry);
    else this.models.set(keyOf(c), entry);
  }

  /** Returns the blocking exhaustion for a candidate, dropping entries whose reset time has passed. */
  blocking(c: CandidateRef, now: number): Exhaustion | undefined {
    for (const [map, key] of [
      [this.providers, c.provider],
      [this.models, keyOf(c)],
    ] as const) {
      const e = map.get(key);
      if (!e) continue;
      if (e.reset?.at !== undefined && e.reset.at <= now) {
        map.delete(key);
        continue;
      }
      return e;
    }
    return undefined;
  }

  newPrompt() {
    this.triedThisPrompt.clear();
  }

  reset() {
    this.providers.clear();
    this.models.clear();
    this.triedThisPrompt.clear();
  }
}

export interface SelectOptions {
  current?: CandidateRef;
  /** Whether a candidate's provider has usable credentials (models.json/auth.json). */
  isReady: (c: CandidateRef) => Promise<boolean> | boolean;
  /** Whether the model exists in the registry. */
  exists: (c: CandidateRef) => boolean;
  now: number;
  /** Ignore triedThisPrompt (used for /failover next and preferPrimary). */
  ignoreTried?: boolean;
}

export interface SkipReason {
  candidate: string;
  why: string;
}

/**
 * Walk the candidate list in priority order and return the first usable one that is not the
 * current model, not already tried for this prompt, not exhausted, present, and authenticated.
 * Candidates found to be unauthenticated are marked unavailable so they are not probed again.
 */
export async function selectNext(
  candidates: CandidateRef[],
  state: FailoverState,
  opts: SelectOptions,
): Promise<{ next?: CandidateRef; skipped: SkipReason[] }> {
  const skipped: SkipReason[] = [];
  for (const c of candidates) {
    const k = keyOf(c);
    if (opts.current && keyOf(opts.current) === k) continue;
    if (!opts.ignoreTried && state.triedThisPrompt.has(k)) {
      skipped.push({ candidate: k, why: "already tried for this prompt" });
      continue;
    }
    const block = state.blocking(c, opts.now);
    if (block) {
      skipped.push({ candidate: k, why: `${block.kind}: ${block.reason}${block.reset ? `, resets ${block.reset.text}` : ""}` });
      continue;
    }
    if (!opts.exists(c)) {
      skipped.push({ candidate: k, why: "model not found in pi --list-models" });
      continue;
    }
    let ready = false;
    try {
      ready = await opts.isReady(c);
    } catch {
      ready = false;
    }
    if (!ready) {
      state.mark(c, { kind: "unavailable", scope: "provider", reason: "provider not authenticated" }, opts.now);
      skipped.push({ candidate: k, why: "provider not authenticated" });
      continue;
    }
    return { next: c, skipped };
  }
  return { skipped };
}

/** The first candidate (in priority order, before `current`) that is no longer blocked, for switching back. */
export function higherPriorityRecovered(
  candidates: CandidateRef[],
  state: FailoverState,
  current: CandidateRef,
  now: number,
): CandidateRef | undefined {
  for (const c of candidates) {
    if (keyOf(c) === keyOf(current)) return undefined;
    if (!state.blocking(c, now)) return c;
  }
  return undefined;
}

export function exhaustionSummary(state: FailoverState, now: number): string[] {
  const lines: string[] = [];
  for (const [provider, e] of state.providers) {
    if (e.reset?.at !== undefined && e.reset.at <= now) continue;
    lines.push(`${provider}: ${e.reason}${e.reset ? `, resets ${e.reset.text}` : ", reset time unknown"}`);
  }
  for (const [model, e] of state.models) {
    if (e.reset?.at !== undefined && e.reset.at <= now) continue;
    lines.push(`${model}: ${e.reason}${e.reset ? `, resets ${e.reset.text}` : ""}`);
  }
  return lines;
}

/** Text to re-send after a switch. Verbatim when nothing ran yet, otherwise ask the new model to continue. */
export function resendText(original: string, fromModel: string, reason: string, hadToolActivity: boolean): string {
  if (!hadToolActivity) return original;
  return (
    `[subscription-failover] The previous model (${fromModel}) stopped mid-task: ${reason}. ` +
    `Check the work already done above and continue the original request from where it stopped:\n\n${original}`
  );
}
