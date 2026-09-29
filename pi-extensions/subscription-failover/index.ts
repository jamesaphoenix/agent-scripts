/**
 * subscription-failover extension for pi
 * ----------------------------------------
 * When the active model's provider fails with a usage limit / quota, rate limit (429),
 * overload (529) or auth error (401/403, expired OAuth), switch to the next ready candidate
 * from an ordered list and re-send the interrupted prompt, once per candidate per prompt.
 *
 * Config (first match wins for the candidate list):
 *   PI_FAILOVER_MODELS="anthropic/claude-opus-5-5,openai-codex/gpt-6-astra"
 *   ~/.pi/agent/subscription-failover.json  (or PI_FAILOVER_CONFIG=/path.json)
 *   built-in defaults (see DEFAULT_CANDIDATES in lib.ts)
 * PI_FAILOVER_DISABLED=1 turns it off.
 *
 * Commands: /failover [status|reset|next]
 *
 * Source of truth: agent-scripts/pi-extensions/subscription-failover (symlinked by dotfiles/install.sh).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  classifyAssistant,
  exhaustionSummary,
  extractReset,
  FailoverState,
  higherPriorityRecovered,
  keyOf,
  loadConfig,
  resendText,
  selectNext,
  type CandidateRef,
  type Classification,
  type FailoverConfig,
  type ResetInfo,
} from "./lib.ts";

const TAG = "subscription-failover";

type AnyModel = NonNullable<ExtensionContext["model"]>;
type ImagePart = { type: "image"; [k: string]: unknown };

interface PendingSwitch {
  from: string;
  to: string;
  cls: Classification;
  reset?: ResetInfo;
}

function configPath(): string {
  return process.env.PI_FAILOVER_CONFIG || join(getAgentDir(), "subscription-failover.json");
}

function readConfigFile(path: string): string | undefined {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return undefined;
  }
}

function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(fallback), ms);
    t.unref?.();
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      () => {
        clearTimeout(t);
        resolve(fallback);
      },
    );
  });
}

export default function subscriptionFailover(pi: ExtensionAPI) {
  const state = new FailoverState();
  let config: FailoverConfig = loadConfig(process.env, readConfigFile(configPath()), configPath()).config;
  let configWarnings: string[] = [];

  // Per-prompt tracking.
  let original: { text: string; images?: ImagePart[] } | undefined;
  let toolActivity = false;
  let ownResendPending = false;
  let lastAssistant: { role: string; stopReason?: string; errorMessage?: string } | undefined;
  let lastStatus: number | undefined;
  let lastHeaders: Record<string, string> | undefined;
  let pending: PendingSwitch | undefined;
  let exhaustedThisPrompt = false;
  let failedOverAway = false;
  let lastResendTo: string | undefined;

  // Sequencing for print mode, where we must wait for the re-sent run before pi exits.

  const say = (ctx: ExtensionContext, msg: string, level: "info" | "warning" | "error" = "info") => {
    if (ctx.hasUI) ctx.ui.notify(msg, level);
    else process.stderr.write(`[${TAG}] ${msg}\n`);
  };

  const refOf = (m: AnyModel | undefined): CandidateRef | undefined =>
    m ? { provider: m.provider, model: m.id } : undefined;

  const isReady = async (ctx: ExtensionContext, c: CandidateRef): Promise<boolean> => {
    const model = ctx.modelRegistry.find(c.provider, c.model);
    if (!model) return false;
    if (!ctx.modelRegistry.hasConfiguredAuth(model)) return false;
    const auth = await withTimeout(ctx.modelRegistry.getApiKeyAndHeaders(model), config.readyTimeoutMs, {
      ok: false as const,
      error: "timed out",
    });
    return auth.ok;
  };

  /** Select and activate the next usable candidate. Returns the activated candidate, or undefined. */
  const switchToNext = async (
    ctx: ExtensionContext,
    current: CandidateRef | undefined,
    ignoreTried = false,
  ): Promise<CandidateRef | undefined> => {
    for (let guard = 0; guard <= config.candidates.length; guard++) {
      const { next } = await selectNext(config.candidates, state, {
        current,
        now: Date.now(),
        ignoreTried,
        exists: (c) => !!ctx.modelRegistry.find(c.provider, c.model),
        isReady: (c) => isReady(ctx, c),
      });
      if (!next) return undefined;
      const model = ctx.modelRegistry.find(next.provider, next.model);
      let ok = false;
      try {
        ok = !!model && (await pi.setModel(model));
      } catch {
        ok = false;
      }
      if (ok) {
        state.triedThisPrompt.add(keyOf(next));
        return next;
      }
      state.mark(next, { kind: "unavailable", scope: "provider", reason: "setModel refused (no credentials)" }, Date.now());
    }
    return undefined;
  };

  pi.on("session_start", (_event, ctx) => {
    const loaded = loadConfig(process.env, readConfigFile(configPath()), configPath());
    config = loaded.config;
    configWarnings = loaded.warnings;
    for (const w of configWarnings) say(ctx, w, "warning");
  });

  pi.on("input", async (event, ctx) => {
    if (!config.enabled || event.source === "extension" || event.streamingBehavior) return;
    ownResendPending = false; // a real user prompt: never treat it as our re-send
    const current = refOf(ctx.model);
    if (!current) return;
    const isCandidate = config.candidates.some((c) => keyOf(c) === keyOf(current));
    if (!isCandidate) return;
    const now = Date.now();
    // Current model's provider lost its credentials, or is known to be exhausted: move before sending.
    const currentModel = ctx.model!;
    if (!ctx.modelRegistry.hasConfiguredAuth(currentModel) || state.blocking(current, now)) {
      const next = await switchToNext(ctx, current, true);
      if (next) {
        failedOverAway = true;
        say(ctx, `${keyOf(current)} is not usable right now; using ${keyOf(next)} instead.`, "warning");
      }
      return;
    }
    // A higher-priority candidate has recovered (its reset time passed): switch back.
    if (config.preferPrimary && failedOverAway) {
      const back = higherPriorityRecovered(config.candidates, state, current, now);
      if (back && (await isReady(ctx, back))) {
        const model = ctx.modelRegistry.find(back.provider, back.model);
        if (model && (await pi.setModel(model))) {
          failedOverAway = keyOf(back) !== keyOf(config.candidates[0]);
          say(ctx, `${keyOf(back)} is available again; switched back from ${keyOf(current)}.`);
        }
      }
    }
  });

  pi.on("before_agent_start", (event, ctx) => {
    if (ownResendPending) {
      ownResendPending = false;
      return;
    }
    original = { text: event.prompt, images: event.images as ImagePart[] | undefined };
    state.newPrompt();
    toolActivity = false;
    pending = undefined;
    lastResendTo = undefined;
    exhaustedThisPrompt = false;
    const current = refOf(ctx.model);
    if (current) state.triedThisPrompt.add(keyOf(current));
  });

  pi.on("turn_start", () => {
    lastStatus = undefined;
    lastHeaders = undefined;
  });

  pi.on("after_provider_response", (event) => {
    lastStatus = event.status;
    lastHeaders = event.headers as Record<string, string> | undefined;
  });

  pi.on("message_end", (event) => {
    const m = event.message as { role: string; stopReason?: string; errorMessage?: string };
    if (m.role === "toolResult") toolActivity = true;
    if (m.role === "assistant") lastAssistant = m;
  });

  pi.on("agent_end", async (event, ctx) => {
    if (!config.enabled) return;
    const msgs = event.messages as Array<{ role: string; stopReason?: string; errorMessage?: string }>;
    const last = [...msgs].reverse().find((m) => m.role === "assistant");
    const cls = classifyAssistant(last, lastStatus);
    if (!cls) return;
    const current = refOf(ctx.model);
    if (!current) return;
    const now = Date.now();
    const reset = extractReset(last?.errorMessage, lastHeaders, now);
    state.mark(current, cls, now, reset);
    state.triedThisPrompt.add(keyOf(current));
    // Switch now: if pi auto-retries this error, the retry already lands on the new model.
    // If it does not, agent_settled re-sends the prompt.
    const next = await switchToNext(ctx, current);
    if (next) {
      failedOverAway = true;
      pending = { from: keyOf(current), to: keyOf(next), cls, reset };
      say(
        ctx,
        `${keyOf(current)}: ${cls.reason}${reset ? ` (resets ${reset.text})` : ""}. Switching to ${keyOf(next)}.`,
        "warning",
      );
    } else {
      exhaustedThisPrompt = true;
    }
  });

  const resend = (p: PendingSwitch) => {
    if (!original) return false;
    const text = resendText(original.text, p.from, p.cls.reason, toolActivity);
    ownResendPending = true;
    toolActivity = false;
    if (original.images?.length) {
      pi.sendUserMessage([{ type: "text", text }, ...original.images] as never);
    } else {
      pi.sendUserMessage(text);
    }
    return true;
  };

  pi.on("agent_settled", async (_event, ctx) => {
    {
      for (let guard = 0; guard <= config.candidates.length; guard++) {
        const stillFailed = lastAssistant?.stopReason === "error";
        if (pending && !stillFailed) {
          say(ctx, `Recovered on ${pending.to}.`);
          pending = undefined;
          break;
        }
        if (!pending) {
          if (lastResendTo && !stillFailed) say(ctx, `Recovered on ${lastResendTo}.`);
          lastResendTo = undefined;
          if (exhaustedThisPrompt && stillFailed) {
            const lines = exhaustionSummary(state, Date.now());
            say(
              ctx,
              `All failover candidates are exhausted or unavailable. ${lines.length ? lines.join("; ") : ""}`.trim() +
                " Run /failover status for details, /failover reset after a limit resets.",
              "error",
            );
            exhaustedThisPrompt = false;
          }
          break;
        }
        const p = pending;
        pending = undefined;
        if (!resend(p)) break;
        lastResendTo = p.to;
        // Never wait for the re-sent run here. Since pi 0.87, runs requested from an
        // agent_settled handler are deferred until every settled handler returns, so
        // waiting for it to start deadlocks (print mode then timed out or hung). The
        // re-sent run's own agent_settled continues the chain in every mode.
        break;
      }
    }
  });

  pi.registerCommand("failover", {
    description: "Subscription failover: /failover [status|reset|next]",
    getArgumentCompletions: (prefix: string) => {
      const items = ["status", "reset", "next"].filter((s) => s.startsWith(prefix)).map((s) => ({ value: s, label: s }));
      return items.length ? items : null;
    },
    handler: async (args, ctx) => {
      const out = (msg: string, level: "info" | "warning" | "error" = "info") => {
        if (ctx.hasUI) ctx.ui.notify(msg, level);
        else process.stdout.write(`${msg}\n`);
      };
      const sub = (args ?? "").trim().split(/\s+/)[0] || "status";
      const current = refOf(ctx.model);
      const now = Date.now();
      if (sub === "reset") {
        state.reset();
        failedOverAway = false;
        const first = await switchToNext(ctx, undefined, true);
        out(`Failover state cleared.${first ? ` Active model: ${keyOf(first)}.` : " No candidate is ready."}`);
        return;
      }
      if (sub === "next") {
        const next = await switchToNext(ctx, current, true);
        if (next) failedOverAway = true;
        out(next ? `Switched to ${keyOf(next)}.` : "No other candidate is ready.", next ? "info" : "warning");
        return;
      }
      const lines = [
        `subscription-failover: ${config.enabled ? "enabled" : "disabled (PI_FAILOVER_DISABLED=1 or config)"}`,
        `config: ${config.source} (file: ${configPath()})`,
        `current: ${current ? keyOf(current) : "(none)"}`,
        "candidates:",
      ];
      for (const c of config.candidates) {
        const model = ctx.modelRegistry.find(c.provider, c.model);
        const block = state.blocking(c, now);
        let status: string;
        if (!model) status = "not in model list";
        else if (block) status = `${block.kind}: ${block.reason}${block.reset ? `, resets ${block.reset.text}` : ""}`;
        else if (!ctx.modelRegistry.hasConfiguredAuth(model)) status = "not authenticated";
        else status = "ready";
        const marker = current && keyOf(current) === keyOf(c) ? "*" : " ";
        lines.push(` ${marker} ${keyOf(c)}  ${status}`);
      }
      for (const w of configWarnings) lines.push(`warning: ${w}`);
      out(lines.join("\n"));
    },
  });
}
