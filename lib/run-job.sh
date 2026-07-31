#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'HELP'
Usage:
  run-job.sh --id job-id [--state-dir path] [--cwd path] [--timeout-seconds n] -- command...

Runs a command with a lock, timestamped logs, exit-code capture, and a JSON
summary. This is for host-level loops and launchd jobs.
HELP
}

JOB_ID=""
STATE_DIR=""
CWD=""
TIMEOUT_SECONDS=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --id)
      JOB_ID="$2"
      shift 2
      ;;
    --state-dir)
      STATE_DIR="$2"
      shift 2
      ;;
    --cwd)
      CWD="$2"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$JOB_ID" ]; then
  echo "Missing --id" >&2
  usage >&2
  exit 2
fi

if [ "$#" -eq 0 ]; then
  echo "Missing command after --" >&2
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${STATE_DIR:-$ROOT_DIR/state/jobs/$JOB_ID}"
LOG_DIR="$STATE_DIR/logs"
RUN_DIR="$STATE_DIR/runs"
LOCK_DIR="$STATE_DIR/$JOB_ID.lock"
LAST_RUN="$STATE_DIR/last-run.json"

mkdir -p "$LOG_DIR" "$RUN_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  echo "Job $JOB_ID is already running${lock_pid:+ with pid $lock_pid}" >&2
  exit 75
fi

cleanup_lock() {
  rm -rf "$LOCK_DIR"
}
trap cleanup_lock EXIT INT TERM HUP

printf '%s\n' "$$" > "$LOCK_DIR/pid"

run_stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
start_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log_file="$LOG_DIR/${run_stamp}.log"
summary_file="$RUN_DIR/${run_stamp}.json"
command_text="$*"

json_escape() {
  sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/	/\\t/g'
}

exec > >(tee -a "$log_file") 2>&1

echo "job_id=$JOB_ID"
echo "started_at=$start_iso"
echo "cwd=${CWD:-$(pwd)}"
echo "command=$command_text"

if [ -n "$CWD" ]; then
  cd "$CWD"
fi

set +e
if [ -n "$TIMEOUT_SECONDS" ]; then
  if command -v timeout >/dev/null 2>&1; then
    timeout "$TIMEOUT_SECONDS" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$TIMEOUT_SECONDS" "$@"
  else
    echo "WARN: timeout requested but neither timeout nor gtimeout is available; running without a timeout" >&2
    "$@"
  fi
else
  "$@"
fi
exit_code=$?
set -e

end_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
status="ok"
if [ "$exit_code" -ne 0 ]; then
  status="failed"
fi

{
  echo "{"
  echo "  \"jobId\": \"$(printf '%s' "$JOB_ID" | json_escape)\","
  echo "  \"status\": \"$status\","
  echo "  \"startedAt\": \"$start_iso\","
  echo "  \"endedAt\": \"$end_iso\","
  echo "  \"exitCode\": $exit_code,"
  echo "  \"cwd\": \"$(printf '%s' "${CWD:-$(pwd)}" | json_escape)\","
  echo "  \"command\": \"$(printf '%s' "$command_text" | json_escape)\","
  echo "  \"logFile\": \"$(printf '%s' "$log_file" | json_escape)\""
  echo "}"
} > "$summary_file"
cp "$summary_file" "$LAST_RUN"

echo "ended_at=$end_iso"
echo "exit_code=$exit_code"
echo "summary=$summary_file"
echo "log=$log_file"

exit "$exit_code"
