# Octospark Temporal Route Health

Checks the Mac Studio host route and normal TCP connectivity to the Temporal server (Hetzner `temporal-server` over Tailscale), then checks the same TCP dependency from inside the Octospark API container.

This intentionally does not use `tailscale ping`, because ping can succeed while a normal TCP route is stale or bypasses the `utun` interface.

Default target:

```bash
100.116.99.19:7233
```

The script first tries to load `TEMPORAL_ADDRESS` from the `octospark-services` 1Password item when the cached service account token is available, so an address change made in 1Password propagates here with no code edit. If that is unavailable, it uses the fallback target above. The retired Synology NAS instance (`100.123.72.113:7233`) is never used.

Install on the Mac Studio through the central registry:

```bash
scripts/install-launchd-tasks.sh --task octospark-temporal-route-health
```

Manual check:

```bash
agent-loops/octospark-temporal-route-health/check.sh
```
