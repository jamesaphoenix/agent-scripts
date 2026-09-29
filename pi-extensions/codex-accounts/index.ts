import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * Extra ChatGPT (Codex) subscription accounts for pi.
 *
 * pi stores ONE credential per provider id in ~/.pi/agent/auth.json, so a
 * second `/login openai-codex` replaces the first account. This extension
 * registers additional providers (openai-codex-2, openai-codex-3, ...) that
 * wrap pi's own built-in Codex provider: same ChatGPT OAuth login, same
 * streaming API, same model catalogue, but a distinct provider id, so each
 * account's tokens live under their own auth.json key.
 *
 * Log each extra account in once per machine with `/login openai-codex-2`
 * (tokens rotate on refresh, so never copy auth.json between machines), then
 * list it in the subscription-failover order, e.g.
 *   PI_FAILOVER_MODELS="anthropic/claude-opus-5-5,openai-codex/gpt-6-astra,openai-codex-2/gpt-6-astra"
 *
 * Config: PI_CODEX_EXTRA_ACCOUNTS = number of extra accounts (default 1, max 5).
 */

const BASE_PROVIDER_ID = "openai-codex";
const CODEX_PROVIDER_MODULE = join("node_modules", "@earendil-works", "pi-ai", "dist", "providers", "openai-codex.js");
const MAX_EXTRA_ACCOUNTS = 5;

export const extraAccountCount = (raw: string | undefined): number => {
  if (raw === undefined || raw.trim() === "") return 1;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed < 0) return 1;
  return Math.min(parsed, MAX_EXTRA_ACCOUNTS);
};

export const extraAccountIds = (count: number): string[] =>
  Array.from({ length: count }, (_, index) => `${BASE_PROVIDER_ID}-${index + 2}`);

// Structural view of the pi-ai Provider this extension needs. pi-ai does not
// export the built-in Codex provider from its package root (only subpaths,
// which extensions cannot import), so it is loaded from pi's install dir.
type AnyFn = (...args: never[]) => unknown;
interface CodexModel { readonly provider: string; readonly [key: string]: unknown }
interface CodexProvider {
  readonly id: string;
  readonly name: string;
  readonly baseUrl?: string;
  readonly headers?: unknown;
  readonly auth: unknown;
  getModels(): readonly CodexModel[];
  refreshModels?: AnyFn;
  filterModels?(models: readonly CodexModel[], credential: unknown): readonly CodexModel[];
  stream(model: CodexModel, context: unknown, options?: unknown): unknown;
  streamSimple(model: CodexModel, context: unknown, options?: unknown): unknown;
  fetchDeferred?(model: CodexModel, handle: unknown, options?: unknown): unknown;
  cancelDeferred?(model: CodexModel, handle: unknown, options?: unknown): unknown;
}

/**
 * Find pi's own copy of pi-ai next to the running pi binary (or PI_AI_CODEX_MODULE),
 * so the wrapped provider is exactly the one pi ships.
 */
export const locateCodexProviderModule = (
  entry: string | undefined = process.argv[1],
  override: string | undefined = process.env.PI_AI_CODEX_MODULE
): string | undefined => {
  if (override) return existsSync(override) ? override : undefined;
  if (!entry) return undefined;
  let dir: string;
  try {
    dir = dirname(realpathSync(entry));
  } catch {
    return undefined;
  }
  for (let depth = 0; depth < 6; depth++) {
    const candidate = join(dir, CODEX_PROVIDER_MODULE);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return undefined;
};

const loadBaseCodexProvider = async (): Promise<CodexProvider | undefined> => {
  const modulePath = locateCodexProviderModule();
  if (!modulePath) return undefined;
  try {
    const mod = (await import(pathToFileURL(modulePath).href)) as { openaiCodexProvider?: () => CodexProvider };
    return mod.openaiCodexProvider?.();
  } catch {
    return undefined;
  }
};

/**
 * A provider that delegates everything to pi's built-in Codex provider but
 * reports its own id, so pi resolves auth (and the auth.json entry) per
 * account. Models are re-labelled with the new provider id for model
 * selection, and handed back to the base provider unchanged when streaming.
 */
export const makeCodexAccountProvider = (id: string, base: CodexProvider): CodexProvider => {
  const toAccount = (model: CodexModel): CodexModel => ({ ...model, provider: id }) as CodexModel;
  const toBase = <M extends CodexModel>(model: M): M => ({ ...model, provider: BASE_PROVIDER_ID }) as M;
  const accountNumber = id.slice(BASE_PROVIDER_ID.length + 1);

  const provider: CodexProvider = {
    id,
    name: `${base.name} (account ${accountNumber})`,
    baseUrl: base.baseUrl,
    headers: base.headers,
    auth: base.auth,
    getModels: () => base.getModels().map(toAccount),
    ...(base.refreshModels ? { refreshModels: base.refreshModels.bind(base) } : {}),
    ...(base.filterModels
      ? {
          filterModels: (models, credential) =>
            base.filterModels!(models.map(toBase), credential).map(toAccount)
        }
      : {}),
    stream: (model, context, options) => base.stream(toBase(model), context, options),
    streamSimple: (model, context, options) => base.streamSimple(toBase(model), context, options),
    ...(base.fetchDeferred
      ? { fetchDeferred: (model, handle, options) => base.fetchDeferred!(toBase(model), handle, options) }
      : {}),
    ...(base.cancelDeferred
      ? { cancelDeferred: (model, handle, options) => base.cancelDeferred!(toBase(model), handle, options) }
      : {})
  };
  return provider;
};

export default async function codexAccounts(pi: ExtensionAPI): Promise<void> {
  if (process.env.PI_CODEX_ACCOUNTS_DISABLED === "1") return;
  const ids = extraAccountIds(extraAccountCount(process.env.PI_CODEX_EXTRA_ACCOUNTS));
  if (ids.length === 0) return;
  const register = (base: CodexProvider) => {
    for (const id of ids) pi.registerProvider(makeCodexAccountProvider(id, base) as never);
  };

  // Preferred: register during startup so `pi --list-models`, `pi auth check`
  // and `/login openai-codex-2` all see the extra accounts.
  const base = await loadBaseCodexProvider();
  if (base) {
    register(base);
    return;
  }

  // Fallback if a pi upgrade moves pi-ai: wrap the live provider once a
  // session starts (registrations after startup take effect immediately).
  pi.on("session_start", async (_event, ctx) => {
    const live = ctx.modelRegistry.getProvider(BASE_PROVIDER_ID) as unknown as CodexProvider | undefined;
    if (live) register(live);
    else ctx.ui.notify("codex-accounts: built-in openai-codex provider not found; extra Codex accounts unavailable", "warning");
  });
}
