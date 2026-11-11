# Shared host OpenTelemetry collectors

Owner: `jamesaphoenix/agent-scripts`, directory `host-observability`.
One Collector process per host, with fixed receiver-to-project pipelines. A caller's
resource attributes never select an exporter. Do not deploy application-owned
sidecars alongside these collectors after migrating that application.

## Synology

`build.py` composes the reviewed profiles into the single pinned Collector config.
`compose.synology.json` preserves existing Octospark and Trace Learn ingress ports,
Docker/host collection and credentials. It adds a private GoWithSolar receiver
`jud-otel:4322` on `solar-roi`. It is not published through a tunnel or host port.

GoWithSolar retains its approved privacy/cardinality filters, five-minute trace
sampling, WARN log threshold, and dedicated bounded disk queues. Its metric batch
limit is 200 data points. Existing Google Managed Prometheus pipelines remain
unbatched. Other projects have separate bounded persistent exporter queues.
A common 768 MiB container limit bounds the shared failure domain.

Octospark and Trace Learn traces are sampled only here; their SDKs stay `always_on`.
Each trace pipeline first drops parentless, non-error `db.repository` spans from
background work, then runs its own `tail_sampling/<route>` instance (per-trace state
is never shared between projects). It keeps every trace containing an ERROR span,
every trace with an `http.response.status_code` of 500 to 599, and every trace slower
than 5 s, and keeps 10% of the rest. Memory: `decision_wait` is 10 s and `num_traces`
10000 per route, so at roughly 20 new traces per second each instance holds about
200 traces in steady state. The cap and `maximum_trace_size_bytes` (256 KiB) bound
bursts, and the shared memory limiter (640 MiB, under the 768 MiB container limit and
576 MiB GOMEMLIMIT) still applies. GoWithSolar keeps its own sampler.
This policy takes effect only after the collector is redeployed with the compose
command below. The tail-sampling `dropped_too_early` counter has no per-processor
label, so the GoWithSolar self-metric for it now counts all five samplers.

The host uses its existing Octospark service account through ADC.
The additional GoWithSolar grants are recorded in `iam.json`; no existing project
access or Terraform-managed collector identity was removed. Explicit project
exporters and isolated receivers determine destination. That identity has only
telemetry write and service-usage access added to GoWithSolar. Credentials stay in
the existing private host mount; no credential values are source controlled.
The Collector version's Google auth extension uses process-wide ADC, so this is
one trusted host identity, not separate credentials for each application.

Build and test:

```sh
python3 -m unittest discover -s host-observability -p 'test_*.py'
python3 host-observability/build.py > /private/tmp/host-otel-config.json
```

Validate using the exact pinned image before deployment, with a read-only hostfs
mount required by hostmetrics. Render config and compose into a new release under
`/volume1/docker/jud/observability/releases/`, preserve the previous release, and
run `docker compose -p jud-observability -f compose.json up -d --no-deps host-collector` there.
The service is named `host-collector` and its container `jud-host-otel`: do not
name the service `otel-collector`, because Compose automatically publishes that
DNS alias on every attached network and would collide with the legacy solar route.
Deploy/restart no application as part of that command. Health is available only
on `127.0.0.1:32133/health/status`.

The fixed IP-free Docker network bindings survive container replacement. GoWithSolar
Spring explicitly permits `http://jud-otel:4322`; the prior localhost and sidecar
endpoints remain valid for rollback. Change the private config-tree endpoint only
after that application version is live. Verify telemetry and drain the old queue
before disabling its restart policy and stopping the old collector. Retain it and
its config/queues until the migration evidence is reviewed. Roll back by restoring
the former Spring endpoint and previous collector compose release.

The baseline profiles capture the inspected 29 October configuration. Future
collector policy changes belong here; application repositories own instrumentation
and the published telemetry contract, not host process deployment. Regenerate the
GoWithSolar profile deliberately when its approved metric inventory changes.

## MinisForum (A Series)

`python3 build.py --profile minisforum` composes ONE collector for all four product
routes, and `python3 build.py --profile minisforum --compose` (checked in as
`compose.minisforum.json`) is its compose file. It reuses the Synology routes
unchanged: the same per-route `tail_sampling/<route>`, the same exporters and GCP
projects (`octospark-staging`, `octospark-prod`, `tracelearn-staging`, `tracelearn-prod`),
and no GoWithSolar route, so there is no second collector.

| Item | MinisForum value |
|---|---|
| Receivers | Octospark staging `4318`, prod `4319`; Trace Learn staging `4320`, prod `4321` (on the `jud-observability` network as `jud-otel`) |
| Native ingress | `100.124.34.45:32018`, `:32019`, `:32118`, `:32119` (Tailscale IP only) |
| Health | `127.0.0.1:32133` to container `13133` (`/health/status`) |
| Image | the Synology digest-pinned `0.163.1@sha256:1175b030...` |
| Limits | `mem_limit` and `memswap_limit` 768m, `cpus` 1, `GOMEMLIMIT=576MiB`, `GOMAXPROCS=1`, memory limiter 640 MiB |
| Slice | `cgroup_parent: jud-observability.slice` (see `agent-loops/a-series-ci/slices/`) |
| docker_stats | Docker API `1.44` (Docker 29 refuses older clients; Synology stays on `1.41`), with `filter/containers-<route>` keeping only containers named `<route>-*` per route |
| Host identity | `k8s.cluster.name` and `service.instance.id` are `minisforum`, not `synology-primary` |

The native OTLP guard (`native_allowed_hosts`, firewall chain `JUD-STAGING-OTLP` in the
deployed `current.json`) is outside this compose file and must be extended to the two new
production ports. Runs as `10001:10001` with `group_add: ["115"]` (the host's `docker`
group) instead of root; mounts only `/proc` and `/sys` instead of `/`.
`hostmetrics` and the container metrics are attributed to `octospark-prod`'s pipeline as on
Synology, because the process is shared.

### What changes against what is deployed

`python3 diff-deployed.py jamesaphoenix@jamesaphoenix-ai-series` (read-only over SSH:
`cat /srv/jud/observability/current.json`, then `docker inspect jud-host-otel` with a fixed
template, because the release `compose.json` and `config.json` are root-only) prints the
diff. On 11 November 2026 it showed: routes grow from two staging routes to four; native
ingress `127.0.0.1:32018` and `127.0.0.1:32118` go away and `:32019`, `:32119` plus
`:32118` on the Tailscale IP appear (host-local clients must use the Tailscale IP or
`jud-otel`); `GOMAXPROCS` 2 to 1; `cpu_shares` 256 removed (the slice weight replaces
it); new `cgroup_parent` and `group_add`; user `0:0` to `10001:10001`; `/` mount replaced
by `/proc` and `/sys`; queue directory `queue-staging` to `queue-shared` and credentials from
the per-release directory to `/srv/jud/observability/credentials`. Image, memory limit,
CPU limit, restart policy, `no-new-privileges`, `cap_drop: ALL`, logging and `GOMEMLIMIT`
are identical.

### Apply and rollback

1. `python3 -m unittest discover -s host-observability -p 'test_*.py'`.
2. On the host create `/srv/jud/observability/queue-shared` and
   `/srv/jud/observability/credentials` owned by `10001:10001` (copy the existing release's
   credentials; never into git). Install the `jud-observability.slice`.
3. Render `config.json` (`build.py --profile minisforum`) and `compose.json` into a new
   `releases/<id>/`, validate with the pinned image, keep the previous release and
   `current.json`, then `docker compose -p jud-observability -f compose.json up -d --no-deps host-collector`.
4. Check `curl -s 127.0.0.1:32133/health/status`, `docker stats jud-host-otel`,
   `systemd-cgls -u jud-observability.slice`, then telemetry for each route in its GCP project.
5. Rollback: re-run step 3 from the previous release directory; its queue directory is
   untouched. Retain the old queue until the new one has drained.

## Hetzner

There is already one Collector process, alongside the shared Temporal service.
`profiles/hetzner-temporal.yaml` is its current reviewed source: it scrapes only
allowlisted Temporal error/backlog metrics and exports to its dedicated infrastructure
project. Preserve `OTEL_METRICS_GCP_PROJECT` separately from the backup bucket project.
The live mount is `/opt/octospark-temporal/otel/otel-collector.gcp.yaml`.
No duplicate Collector was started. Add application ingress and project-specific
pipelines here only when workloads actually run on that host. Do not send all
Synology traffic across the tailnet just to centralise collection.

## Smart Cub Learning / Trace Learn

James confirmed on 29 October that Trace Learn is the backend intended by
Smart Cub Learning in this consolidation. Its production and staging services
already use the shared Synology routes (`jud-otel:4321` and `jud-otel:4320`).
They retain their existing `tracelearn-prod` and `tracelearn-staging` GCP projects.
Do not create another receiver/project/collector for the Smart Cub product name.
Browser/mobile telemetry remains excluded from GMP under the application's policy.
