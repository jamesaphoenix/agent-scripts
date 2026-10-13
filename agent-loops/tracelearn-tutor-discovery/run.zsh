#!/bin/zsh
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
source ~/.zshrc >/dev/null 2>&1
set -euo pipefail
script_dir="${0:A:h}"
scripts_root="${script_dir:h:h}"
source "$scripts_root/lib/op-service-account-env.sh"
agent_scripts_export_op_service_account --required
export OP_BIOMETRIC_UNLOCK_ENABLED=false
export OP_LOAD_DESKTOP_APP_SETTINGS=false
export OP_CACHE=false
unset ANTHROPIC_API_KEY RESEND_API_KEY POSTMARK_SERVER_TOKEN POSTMARK_ACCOUNT_TOKEN CLOUDFLARE_API_TOKEN
run_dir="${TRACELEARN_DISCOVERY_RUN_DIR:-$scripts_root/state/tracelearn-tutors-20261009}"
binary="${TRACELEARN_CRAWLER_BINARY:-/Users/jamesphoenix/agent-runtime/just-understanding-data/tracelearn-outbound/bin/tutor-crawler}"
mkdir -p "$run_dir"
[[ -x "$binary" ]] || { print -u2 "Missing tutor-crawler binary: $binary"; exit 1; }
python3 - "$run_dir/job.json" <<'PY'
import datetime, json, os, sys
path = sys.argv[1]
if not os.path.exists(path):
    now = datetime.datetime.now(datetime.timezone.utc)
    value = {"runId": "bucks-independent-20261009-7h", "startedAt": now.isoformat(), "deadline": (now + datetime.timedelta(hours=7)).isoformat(), "budgetCents": 10000, "requestedOrganisation": "rEo6fixnr7PaiWMiiqaXdyZrrX8TaUui", "delivery": "research artifacts for authenticated import"}
    with open(path, "x") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
PY
run_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runId"])' "$run_dir/job.json")"
deadline="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["deadline"])' "$run_dir/job.json")"
if ! python3 - "$deadline" <<'PY'
import datetime, sys
sys.exit(0 if datetime.datetime.now(datetime.timezone.utc) < datetime.datetime.fromisoformat(sys.argv[1]) else 1)
PY
then
  if [[ -f "$run_dir/discovery.json" ]]; then
    # The runner records the expired state and exports without provider credentials.
    exec "$binary" discover --state "$run_dir/discovery.json" --resume
  fi
  print "Finite discovery window ended; preserving existing research artifacts."
  exit 0
fi
# Credentials stay in 1Password. Authentication failure cannot extend the window.
python3 - <<'PY'
import subprocess
for reference in ["op://api-keys/DataForSEO/login", "op://api-keys/DataForSEO/password"]:
    subprocess.run(["op", "read", reference], check=True, stdout=subprocess.DEVNULL, timeout=30)
PY
if [[ -f "$run_dir/discovery.json" ]]; then
  exec op run --env-file="$script_dir/dataforseo.env" -- env -u OP_SERVICE_ACCOUNT_TOKEN "$binary" discover --state "$run_dir/discovery.json" --resume
fi
exec op run --env-file="$script_dir/dataforseo.env" -- env -u OP_SERVICE_ACCOUNT_TOKEN "$binary" discover --state "$run_dir/discovery.json" --run-id "$run_id" --deadline "$deadline" --budget-cents 10000 --query-interval 5m
