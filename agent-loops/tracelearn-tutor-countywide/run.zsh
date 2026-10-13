#!/bin/zsh
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
set -euo pipefail
script_dir="${0:A:h}"
export TRACELEARN_DISCOVERY_RUN_DIR="${script_dir:h:h}/state/tracelearn-tutors-20261010-countywide"
export TRACELEARN_CRAWLER_BINARY=/Users/jamesphoenix/agent-runtime/just-understanding-data/tracelearn-outbound/bin/tutor-crawler-countywide
python3 - "$TRACELEARN_DISCOVERY_RUN_DIR" <<'PY'
import datetime, hashlib, json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
job = root / "job.json"
if not job.exists():
    if (root / "discovery.json").exists():
        raise SystemExit("Checkpoint has no job metadata; preserve it for repair")
    manifest = json.loads((root / "plan-manifest.json").read_text())
    if hashlib.sha256((root / "queries.json").read_bytes()).hexdigest() != manifest["planSha256"]:
        raise SystemExit("Prepared query plan digest mismatch")
    if not 100 <= manifest["budgetCents"] <= 2000 or manifest["priorExposureCents"] + manifest["budgetCents"] > 10000:
        raise SystemExit("Shared discovery budget exceeded")
    if not (root / "web-seeds.txt").is_file():
        raise SystemExit("Missing reviewed public web seed file")
    now = datetime.datetime.now(datetime.timezone.utc)
    value = {"runId": "bucks-countywide-20261010-10h", "startedAt": now.isoformat(),
             "deadline": (now + datetime.timedelta(hours=10)).isoformat(),
             "budgetCents": manifest["budgetCents"], "priorExposureCents": manifest["priorExposureCents"],
             "queryInterval": "4m", "queryPlan": "queries.json", "seedPlan": "web-seeds.txt",
             "planSha256": manifest["planSha256"],
             "seedSha256": hashlib.sha256((root / "web-seeds.txt").read_bytes()).hexdigest(),
             "requestedOrganisation": "rEo6fixnr7PaiWMiiqaXdyZrrX8TaUui",
             "delivery": "research artifacts for authenticated import"}
    with job.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
value = json.loads(job.read_text())
for filename, field in [("queries.json", "planSha256"), ("web-seeds.txt", "seedSha256")]:
    if hashlib.sha256((root / filename).read_bytes()).hexdigest() != value[field]:
        raise SystemExit("Immutable input digest mismatch: " + filename)
PY
# Reuse the existing secret-safe launcher and immutable checkpoint recovery.
exec /bin/zsh "$script_dir/../tracelearn-tutor-expansion/run.zsh"
