# pi-extensions

Global extensions for [pi](https://pi.dev) (`@earendil-works/pi-coding-agent`, CLI `pi`), kept
here as versioned code and shared by both Macs.

| Extension | What it does |
|---|---|
| `web-search/` | `web_search` (Brave Search API) and `web_fetch` (URL to cleaned text) tools |
| `subscription-failover/` | Switches model when a subscription hits a usage limit, rate limit, overload or auth error, then re-sends the interrupted prompt. Adds `/failover` |
| `codex-accounts/` | Registers extra ChatGPT (Codex) subscription accounts as `openai-codex-2`, ... so more than one can be logged in and failed over to |

## Install and sync

The dotfiles repo installs these. `dotfiles/install.sh` symlinks every directory in
`agent-scripts/pi-extensions/` into `~/.pi/agent/extensions/<name>`, where pi auto-discovers
`*/index.ts`. It finds this repo via `$AGENT_SCRIPTS_DIR`, then the sibling of the dotfiles
checkout, then `~/Desktop/projects/just-understanding-data/agent-scripts`, then the Mac Studio
runtime copy under `~/agent-runtime/`.

- MacBook Pro: author and test here, commit, push.
- Mac Studio: `git pull` in `~/Desktop/projects/just-understanding-data/agent-scripts` (or
  `scripts/deploy-to-mac-studio.sh --live` from the MacBook), then `./install.sh` in dotfiles.
  Because the extensions are symlinks, a pull is enough once the links exist; restart pi or run
  `/reload`.

Never put credentials in this directory. `~/.pi/agent/auth.json` holds each machine's own
rotating OAuth refresh tokens and must stay local (sharing it breaks the other Mac on the next
refresh). `settings.json` stays local too, because pi rewrites it. `dotfiles/pi/models.json`
(custom providers, no secrets) is symlinked by `install.sh`.

## web-search

Tools: `web_search(query, count?)` and `web_fetch(url)`.

The Brave API key is resolved lazily on the first `web_search` call and cached in memory, so pi
startup never waits on 1Password:

1. `BRAVE_API_KEY` environment variable.
2. `op read "op://api-keys/Brave Search/credential"` through the `cli-automation` service account.
   The token comes from `OP_SERVICE_ACCOUNT_TOKEN` or the login keychain service
   `op_service_account_token_cli_automation`. op runs with an isolated empty `HOME` (avoids the
   TCC wedge documented in `lib/op-cli.sh`) and a hard deadline that SIGKILLs its process group.
   No token means no op call: it never falls back to an interactive or biometric sign-in.
   A failed lookup is retried at most once a minute.
3. Deprecated: a plaintext `brave.key` next to the extension or in `~/.pi/agent/`. It warns once.

| Variable | Default | Purpose |
|---|---|---|
| `BRAVE_API_KEY` | unset | Use this key directly |
| `BRAVE_API_KEY_OP_REF` | `op://api-keys/Brave Search/credential` | Other 1Password reference |
| `PI_WEB_SEARCH_OP_TIMEOUT_MS` | `10000` | Deadline for `op read` |
| `PI_WEB_SEARCH_DISABLE_OP` | unset | `1` skips 1Password |
| `OP_BIN` | `/opt/homebrew/bin/op` | op binary |

## subscription-failover

### What triggers it

Only provider errors on the assistant message (`stopReason: "error"`) are classified. Aborts,
tool failures and normal answers never trigger it.

| Kind | Matches | Scope |
|---|---|---|
| `quota` | usage limit, `usage_limit_reached`, `insufficient_quota`, quota exceeded, credit balance too low, billing, "would exceed your account's rate limit" | whole provider |
| `auth` | 401, 403, `authentication_error`, invalid API key or bearer, expired or revoked token, `/login` hints | whole provider |
| `overloaded` | 529, overloaded | that model only |
| `rate_limit` | 429, rate limit, too many requests | whole provider |

Context-window overflows, 413s, 5xx and network errors are left to pi's own retry and compaction.
The last HTTP status seen by `after_provider_response` is used when the error text is vague.

### What it does

1. On `agent_end` with a matching error, it marks the provider (or model) exhausted, records any
   reset time (from `anthropic-ratelimit-unified-reset`, `retry-after`, `x-ratelimit-reset*`
   headers, or text such as "Try again in ~42 min" and `resets_at`), then switches to the next
   ready candidate with `pi.setModel`.
2. If pi auto-retries the error (it does for 429 and 529), that retry already goes to the new
   model. Otherwise, on `agent_settled` it re-sends the interrupted prompt. If tools already ran,
   it asks the new model to continue from where the old one stopped instead of starting over.
3. Each candidate is used at most once per user prompt. When the list runs out it stops and prints
   which providers are exhausted and when they reset.
4. On the next user prompt, if a higher-priority candidate's reset time has passed, it switches
   back (`preferPrimary`).

A candidate counts as ready when it exists in `pi --list-models`, its provider has configured
auth, and a credential probe (`getApiKeyAndHeaders`, which refreshes OAuth) succeeds within
`readyTimeoutMs`. Unready providers are skipped and remembered.

Print mode (`pi -p`) is supported: the extension waits for the re-sent run so pi exits with the
new answer, notices go to stderr, and every wait is bounded, so it never hangs. Keep
`retry.provider.maxRetries` at `0` (the pi default) so usage-limit errors reach pi quickly.

### Config

Candidate order, first match wins:

1. `PI_FAILOVER_MODELS="anthropic/claude-opus-5-5,openai-codex/gpt-6-astra"`
2. `~/.pi/agent/subscription-failover.json` (or the path in `PI_FAILOVER_CONFIG`); see
   `subscription-failover/config.example.json`
3. Defaults: `anthropic/claude-opus-5-5`, `anthropic/claude-sonnet-5`,
   `openai-codex/gpt-6-astra`, `openai-codex/gpt-5.5`

`PI_FAILOVER_DISABLED=1` turns it off. Config file keys: `enabled`, `candidates`,
`preferPrimary`, `startTimeoutMs` (print mode: how long to wait for a re-sent prompt to start),
`readyTimeoutMs`.

### `/failover`

- `/failover` or `/failover status`: candidates, which one is active, readiness, exhaustion and reset times.
- `/failover reset`: forget exhaustion and switch to the first ready candidate.
- `/failover next`: switch to the next ready candidate now.

In print mode: `pi -p "/failover status" </dev/null` (the output goes to stderr).

## codex-accounts

pi keeps one credential per provider id in `~/.pi/agent/auth.json`, so a second `/login openai-codex` replaces the first ChatGPT account. This extension registers extra providers, `openai-codex-2` (and `-3`... if configured), that wrap pi's own built-in Codex provider: same ChatGPT OAuth login, streaming API and model catalogue, but a separate provider id, so each account's tokens have their own `auth.json` entry.

- Log in once per machine: start `pi`, then `/login openai-codex-2` and sign in with the second ChatGPT account. Never copy `auth.json` between machines (refresh tokens rotate).
- Use it like any provider: `pi --provider openai-codex-2 --model gpt-6-astra`, or `/model`.
- `subscription-failover` lists `openai-codex-2/gpt-6-astra` and `openai-codex-2/gpt-5.5` after the first Codex account by default, and skips them until that account is logged in.
- `PI_CODEX_EXTRA_ACCOUNTS=<n>` sets how many extra accounts to register (default 1, max 5); `PI_CODEX_ACCOUNTS_DISABLED=1` turns it off.
- pi-ai does not export the Codex provider from its package root, so the extension loads `node_modules/@earendil-works/pi-ai/dist/providers/openai-codex.js` from pi's own install (override with `PI_AI_CODEX_MODULE`). If a pi upgrade moves it, it falls back to wrapping the live provider at session start.
- `pi --list-models` and `pi auth check` do not load extensions, so they do not show `openai-codex-2`; check it with a print-mode run instead, e.g. `echo ok | pi -p --no-session --provider openai-codex-2 --model gpt-5.5` ("No API key found for openai-codex-2" means it is registered but not logged in yet).
- Using several subscriptions to get around a provider's per-account usage limits may be against that provider's terms; check before relying on it.

## Tests

```bash
python3 -m unittest discover -s tests -v      # includes the pi-extensions suites
node --test tests/pi-extensions/*.test.ts     # unit (Node 22: add --experimental-strip-types)
node --test tests/pi-extensions/e2e-failover.test.mjs
```

The end-to-end suite starts a local OpenAI-compatible server, registers fake providers in a
temporary `PI_CODING_AGENT_DIR/models.json`, and runs real `pi -p --no-session -e
pi-extensions/subscription-failover`. It covers a 429 usage limit, a non-retryable quota error
(prompt re-sent), a 401, an ordinary 400 (no failover) and full exhaustion (exits without
hanging). It uses no real subscription quota.

Always pass `</dev/null` when running `pi -p` from scripts, otherwise pi waits for stdin.
