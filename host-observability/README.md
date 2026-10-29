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

The host uses its existing Octospark service account through ADC. Explicit project
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
run `docker compose -p jud-observability -f compose.json up -d --no-deps` there.
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

## Hetzner

There is already one Collector process, alongside the shared Temporal service.
`profiles/hetzner-temporal.yaml` is its current reviewed source: it scrapes only
allowlisted Temporal error/backlog metrics and exports to its dedicated infrastructure
project. Preserve `OTEL_METRICS_GCP_PROJECT` separately from the backup bucket project.
The live mount is `/opt/octospark-temporal/otel/otel-collector.gcp.yaml`.
No duplicate Collector was started. Add application ingress and project-specific
pipelines here only when workloads actually run on that host. Do not send all
Synology traffic across the tailnet just to centralise collection.

## Smart Cub Learning

No running Smart Cub backend or Collector was found on Synology, Hetzner, A Series
or Mac Studio in the host inventory. Its repository contains deployment templates,
but those alone do not prove a current live endpoint. Confirm the runtime location
before migrating it. Its browser/mobile telemetry must remain excluded from GMP,
as specified by that application's existing policy. Do not guess its GCP project
or reroute another application's port for it.
