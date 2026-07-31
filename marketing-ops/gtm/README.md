# GTM REST workspace control plane

`gtmctl` creates reviewable Google Tag Manager workspaces from a reusable JSON manifest. It can
inspect, plan, apply, and validate the managed analytics resources. It cannot publish a container
or create a container version.

`inventory` and `validate` read the official live-version endpoint and report the live version ID,
name, and tag, trigger, and variable counts. `publishAttempted=false` means the command issued no
publish request. The CLI still has no publish or create-version command.

The control plane deliberately keeps project values outside its source code:

- Account and container numeric IDs are discovered through the API from configured names.
- Workspace numeric IDs are discovered from names or returned when a fresh workspace is created.
- Container IDs, domains, route paths, GA4 measurement IDs, and OAuth secrets are not embedded.
- Every string in a manifest supports strict `${ENV_VAR}` interpolation.
- Any interpolated value that starts with `op://` is read through 1Password CLI without printing it.
- OAuth fields must be environment interpolations or direct `op://` references. Plaintext OAuth
  values in a manifest are rejected.

## Managed resource contract

The compact `managed` section expands into this fixed, reusable set:

- One data layer variable for every configured field.
- One environment lookup variable for the GA4 measurement ID.
- One boot-event trigger, constrained to the configured surfaces.
- When event-parameter allowlists are configured, one scoped trigger for each allowlist,
  constrained by the group's anchored event pattern and the configured surfaces. Every group event
  is first validated against the global event pattern. Legacy manifests keep one generic
  allowlisted trigger.
- Zero or more named exact-event triggers, constrained to the configured surfaces and additional
  manifest conditions.
- One Google tag that reads its destination from the lookup variable and its page location from a
  configured data layer variable.
- When event-parameter allowlists are configured, one GA4 event tag for each allowlist. Each tag
  uses the built-in `Event` variable and attaches only that allowlist's declared parameters. Legacy
  manifests keep one generic event tag.
- Optionally, one Google Ads Conversion Linker tag that fires on the managed boot trigger or a
  named exact-event trigger.
- Optionally, one or more Google Ads conversion tags that fire on named exact-event triggers.
- Optionally, one or more Microsoft UET base and conversion tags that fire on named exact-event
  triggers.

The Google tag sets `allow_google_signals=false` and
`allow_ad_personalization_signals=false` in every project. Audience and ad-personalization features
therefore remain off by default even when analytics consent is available.

The application owns the value named by `managed.pageLocationField`. It must push a sanitized page
location that does not contain query parameters, fragments, click IDs, or other sensitive values.
`gtmctl` requires this field in `managed.dataLayerFields` and maps the Google tag's `page_location`
setting to the corresponding managed data layer variable. The tag does not rely on the browser's
raw page URL for this setting.

Resource names are derived from `managed.resourcePrefix`. Existing resources are matched only by
exact name. No unrelated tag, trigger, or variable is removed or paused. Superseded resources must
be listed explicitly in `superseded.pauseTags`, `deleteTags`, `deleteTriggers`, or
`deleteVariables`. A tag cannot appear in both `pauseTags` and `deleteTags` because those actions
are contradictory.

Managed resources are compared after removing only GTM-owned identity and fingerprint fields.
Additional mutable fields such as blocking triggers, schedules, setup or teardown tags, and firing
options are drift, even when every manifest-owned field still matches. This prevents a manual GTM
edit from changing tag behavior while `plan` or `validate` reports `noop`.

## Project manifest

Copy `example.manifest.json` into the project that owns the tracking contract. Keep the shared CLI
here. The example has no provider IDs or project paths and can be used by any repository.

Important fields:

- `target.accountName` and `target.containerName`: stable human-readable names used for discovery.
- `workspace.compareName`: existing workspace used by `plan` when `--workspace` is omitted.
- `measurementIdMappings`: deployment environment to GA4 destination mapping.
- `dataLayerFields`: the only application fields exposed to the managed tags.
- `pageLocationField`: the data layer field containing the application-sanitized page location.
- `allowedEventPattern`: an anchored regular expression for events the managed GA4 tags may send.
  Alternation must be inside a group, or the pipe must be escaped as a literal. A top-level pipe is
  rejected because `^purchase|page_view$` does not apply both anchors to both branches.
- `eventParameters`: the reusable catalog mapping GA4 parameter names to managed data layer
  fields.
- `eventParameterAllowlists`: optional, mutually exclusive event groups that select parameter
  names from `eventParameters`. Every literal event must match `allowedEventPattern`, and an event
  cannot appear in more than one group. Use these groups whenever events have different shapes.
  GTM version-2 data layer values persist within a document, so a single broad event tag can attach
  stale CTA or purchase values to a later page view. Scoped tags prevent that inheritance without
  clearing fields that another provider tag still needs for the current event. If this field is
  omitted, `gtmctl` preserves the legacy single generic GA4 event tag for compatibility.
- `exactEventTriggers`: reusable logical trigger names, exact data layer event names, and optional
  field conditions. Each condition names a field from `dataLayerFields` and supports `equals`,
  `contains`, `startsWith`, `endsWith`, or `matchRegex`.
- `surfaceField` and `surfaceValues`: an explicit allowlist guard on both firing triggers.
  Values are escaped and compiled into one anchored exact-match regular expression, so a manifest
  can safely allow both a public marketing surface and a separate conversion-confirmation surface.
- `consentTypes`: consent requirements attached to both managed analytics tags.
- `googleAds.enableConversionLinker`: opt in to the managed Google Ads Conversion Linker.
- `googleAds.conversionLinkerTriggerName`: optional exact-event trigger for the linker. Use this to
  separate an independently consented advertising boot from the managed analytics boot. If it is
  omitted, the linker keeps using the managed boot trigger.
- `googleAds.consentTypes`: the non-empty, unique consent requirements for the linker. A typical
  client-side setup uses `ad_storage` independently from the analytics tags' consent requirements.
- `googleAds.conversionActions`: optional Google Ads conversion actions with configured IDs,
  labels, data layer value fields, consent requirements, and an exact-event `triggerName`.
- `microsoftAds.baseTags`: optional Microsoft UET base tags with configured tag IDs, consent
  requirements, queue names, exact-event trigger references, and an explicit
  `enableAutoSpaTracking` choice.
- `microsoftAds.conversionActions`: optional Microsoft UET custom purchase actions with data layer
  value fields, consent requirements, queue names, and exact-event trigger references.

When enabled, the linker is an unpaused built-in GTM `gclidw` tag. It fires on the exact trigger
named by `conversionLinkerTriggerName`, or on the managed boot trigger when the optional field is
omitted. It carries the standard managed note and sets `enableCookieOverrides=false`. Disabling or
removing `googleAds` stops managing the linker but does not delete an existing tag implicitly. Add
its exact name to `superseded.deleteTags` when deliberate cleanup is required.

## Client-side provider tags

Provider tags are opt in. Removing a provider section stops managing those resources but does not
delete existing tags. Put their exact generated names in `superseded.deleteTags` when deletion is
intentional.

Every Microsoft base tag and every Google or Microsoft conversion action references a logical
`triggerName` from `managed.exactEventTriggers`. The CLI creates those triggers first and resolves
their workspace-specific GTM trigger IDs before creating or updating tags. Manifests never store
GTM numeric trigger IDs.

For independently consented client-side tracking, use the managed boot event for analytics and a
separate exact event for advertising. Set `googleAds.conversionLinkerTriggerName` and each
advertising provider base tag to that advertising trigger. The application should enqueue the
advertising consent update before the advertising boot event and emit each purpose's boot event at
most once per document. Route-context updates should omit the `event` key so they cannot refire
page-load tags.

An exact-event trigger always includes the global `surfaceField` and `surfaceValues` allowlist. Its
`conditions` add further AND conditions. Environment gating is therefore explicit manifest data,
for example an `equals` condition on the configured deployment environment field. Provider code
does not contain production, staging, domain, path, or product-specific checks.

Each Google Ads conversion action produces one built-in `awct` tag. The manifest configures:

- `conversionId` and `conversionLabel` as strings, normally supplied by environment interpolation.
- `orderIdField`, `conversionValueField`, and `currencyCodeField` as managed data layer fields.
- `triggerName` and `consentTypes` per action.

The remaining bootstrapped settings are fixed safety defaults: new-customer reporting, product
reporting, shipping data, and restricted data processing are off; Conversion Linker support is on.
The `conversionActions` array can hold separate actions for different environments, products, or
conversion goals, with trigger conditions deciding where each action fires.

Microsoft UET uses the built-in `baut` tag type. Each `baseTags` entry creates a page-load base tag
with consent inheritance and consent updates enabled. Conversion cookies are enabled only after the
tag's configured GTM consent requirements are met. Query strings are removed from reported URLs,
enhanced conversions are off, and the default queue name is `uetq`. Automatic SPA tracking defaults
off for backwards compatibility. Set `enableAutoSpaTracking` to `true` for history-based SPAs that
do not emit their own UET page-view events, following
[Microsoft's current SPA guidance](https://learn.microsoft.com/en-us/advertising/msa-help/hlp_ba_proc_uetv2addtag).
Before enabling it, prove that every navigation from an allowed measurement surface to an
ineligible application, authentication, or learner surface performs a full document load. The base
tag can otherwise continue observing history changes after the GTM trigger that started it. Set
`uetqName` on both the base and conversion entries to use another queue.

Each Microsoft conversion action creates a `CUSTOM` event with action `purchase`. The manifest
maps `goalValueField`, `currencyField`, `eventCategoryField`, `eventLabelField`, and
`transactionIdField` to managed data layer variables. The transaction ID is sent through the UET
`customParamTable` as `transaction_id`. Every conversion queue must match a managed base-tag queue,
which prevents a conversion tag from targeting an uninitialized queue.

Provider IDs and labels are project configuration, not shared defaults. Keep them in project env
templates or 1Password references and interpolate them into the project manifest. The shared CLI,
schema, and example contain no real provider IDs.

The CLI validates the manifest itself with no third-party Python dependencies. The JSON Schema is
included for editor support.

## Install on PATH

Install a real executable entry in `${HOME}/.local/bin`:

```bash
marketing-ops/gtm/install.sh
```

Override the destination when needed:

```bash
GTMCTL_INSTALL_DIR="$HOME/bin" marketing-ops/gtm/install.sh
```

The installer resolves its own checkout and creates a symlink to the repository-owned wrapper. It
does not contain a username or an absolute machine path. Reinstall after moving the checkout.

## OAuth and 1Password

The manifest can point directly to 1Password:

```json
{
  "clientId": "op://VAULT/ITEM/client_id",
  "clientSecret": "op://VAULT/ITEM/client_secret",
  "refreshToken": "op://VAULT/ITEM/refresh_token"
}
```

It can also use environment variables whose values are either injected secrets or `op://` refs:

```bash
export GTM_OAUTH_CLIENT_ID='op://VAULT/ITEM/client_id'
export GTM_OAUTH_CLIENT_SECRET='op://VAULT/ITEM/client_secret'
export GTM_OAUTH_REFRESH_TOKEN='op://VAULT/ITEM/refresh_token'
```

The tool sends OAuth credentials only to the configured Google token endpoint. It never includes
them in command output.

## Commands

Pass the manifest before the subcommand:

```bash
gtmctl --manifest path/to/gtm.manifest.json doctor --offline
gtmctl --manifest path/to/gtm.manifest.json doctor
gtmctl --manifest path/to/gtm.manifest.json inventory
gtmctl --manifest path/to/gtm.manifest.json plan
gtmctl --manifest path/to/gtm.manifest.json apply \
  --workspace "$GTM_REVIEW_WORKSPACE_NAME" \
  --yes
gtmctl --manifest path/to/gtm.manifest.json resume \
  --workspace "$GTM_REVIEW_WORKSPACE_NAME" \
  --yes
gtmctl --manifest path/to/gtm.manifest.json validate \
  --workspace "$GTM_REVIEW_WORKSPACE_NAME"
```

`apply` refuses to reuse an existing workspace. This keeps each run isolated and reviewable. It
creates the workspace, upserts the managed resources, and performs only the explicit superseded
actions in the manifest.

`resume` is the recovery path for an interrupted `apply`. It resolves an existing workspace by
exact name, verifies that its description exactly matches `workspace.description`, then runs the
same idempotent reconciliation used by `apply`. It refuses `Default Workspace`, requires `--yes`,
and never creates a replacement workspace. Already completed managed and superseded actions become
`noop` or `missing` results, so it is safe to run again after another interruption.

GET requests and the OAuth token exchange retry 429, 500, 502, 503, and 504 responses up to six
total attempts. The delay honors a numeric `Retry-After` header when present, otherwise it uses
bounded exponential backoff starting at 1 second and capped at 30 seconds. The default no-header
retry window is 31 seconds. Workspace and resource mutations are never retried
automatically because GTM create operations have no idempotency key and a failed response does not
prove that the mutation failed. After an interrupted mutation, inspect the workspace and use
`resume` against the same dedicated workspace.

`validate` checks managed drift, explicit superseded actions, merge conflicts, and GTM quick
preview compiler output. Quick preview creates only a temporary preview representation. Validation
fails closed if quick preview omits its compiler result or temporary container version, or if the
live-version response omits its version identity. Mutation and validation results report
`publishAttempted: false`, which means this command issued no publish request. It does not claim
that equivalent resources have never been published by another command or user.

There is intentionally no publish or container-version command. Publishing remains a separate,
human-approved action outside this tool.

## Tests

The test suite uses a local mock HTTP server for OAuth and GTM REST calls:

```bash
python3 -m unittest discover -s marketing-ops/gtm/tests -v
```
