#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="$ROOT_DIR/state/project-sync"
LOG_DIR="$STATE_DIR/logs"
RUN_DIR="$STATE_DIR/runs"
LOCK_DIR="$STATE_DIR/macbook-to-mac-studio.lock"
LAST_RUN="$STATE_DIR/last-run.json"

SOURCE_DIR="${PROJECT_SYNC_SOURCE_DIR:-/Users/jamesaphoenix/Desktop/projects/}"
TARGET_SSH="${PROJECT_SYNC_TARGET_SSH:-jamesphoenix@Jamess-Mac-Studio.local}"
TARGET_DIR="${PROJECT_SYNC_TARGET_DIR:-/Users/jamesphoenix/Desktop/projects/}"
TARGET="${TARGET_SSH}:${TARGET_DIR}"

MODE="dry-run"

usage() {
  cat <<'HELP'
Usage:
  macbook-to-mac-studio.sh --dry-run
  macbook-to-mac-studio.sh --live
  macbook-to-mac-studio.sh --status

One-way rsync from MacBook Pro projects to Mac Studio Desktop projects.
The script never deletes files from the Mac Studio target. Studio-only files
are kept because rsync is run without --delete.
HELP
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --live)
      MODE="live"
      shift
      ;;
    --status)
      MODE="status"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$LOG_DIR" "$RUN_DIR"

json_escape() {
  sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e 's/	/\\t/g'
}

show_status() {
  echo "source: $SOURCE_DIR"
  echo "target: $TARGET"
  echo "state: $STATE_DIR"

  if [ -f "$LAST_RUN" ]; then
    echo "last run:"
    cat "$LAST_RUN" | sed 's/^/  /'
  else
    echo "last run: none"
  fi
}

if [ "$MODE" = "status" ]; then
  show_status
  exit 0
fi

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Source directory does not exist: $SOURCE_DIR" >&2
  exit 66
fi

if [ -e "$LOCK_DIR" ] && [ ! -d "$LOCK_DIR" ]; then
  echo "Lock path exists but is not a directory: $LOCK_DIR" >&2
  exit 75
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  echo "Project sync is already running${lock_pid:+ with pid $lock_pid}" >&2
  exit 75
fi

cleanup_lock() {
  rm -rf "$LOCK_DIR"
}
trap cleanup_lock EXIT INT TERM HUP

printf '%s\n' "$$" > "$LOCK_DIR/pid"

run_stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
start_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log_file="$LOG_DIR/${run_stamp}-${MODE}.log"
summary_file="$RUN_DIR/${run_stamp}-${MODE}.json"

exec > >(tee -a "$log_file") 2>&1

echo "project-sync mode=$MODE"
echo "started_at=$start_iso"
echo "source=$SOURCE_DIR"
echo "target=$TARGET"

set +e
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SSH" "mkdir -p '$TARGET_DIR' && test -d '$TARGET_DIR'"
ssh_code=$?
set -e

if [ "$ssh_code" -ne 0 ]; then
  end_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  {
    echo "{"
    echo "  \"mode\": \"$MODE\","
    echo "  \"status\": \"ssh_failed\","
    echo "  \"startedAt\": \"$start_iso\","
    echo "  \"endedAt\": \"$end_iso\","
    echo "  \"source\": \"$(printf '%s' "$SOURCE_DIR" | json_escape)\","
    echo "  \"target\": \"$(printf '%s' "$TARGET" | json_escape)\","
    echo "  \"sshExitCode\": $ssh_code,"
    echo "  \"rsyncExitCode\": null,"
    echo "  \"logFile\": \"$(printf '%s' "$log_file" | json_escape)\""
    echo "}"
  } > "$summary_file"
  cp "$summary_file" "$LAST_RUN"
  echo "SSH preflight failed with exit code $ssh_code"
  exit "$ssh_code"
fi

rsync_args=(
  -az
  --human-readable
  --stats
  --partial
  --exclude node_modules/
  --exclude .next/
  --exclude dist/
  --exclude build/
  --exclude .turbo/
  --exclude .cache/
  --exclude .venv/
  --exclude __pycache__/
  --exclude .pytest_cache/
  --exclude .DS_Store
  --exclude .playwright-cli/
  --exclude coverage/
  --exclude test-results/
  --exclude playwright-report/
  --exclude .vite/
  --exclude .parcel-cache/
  --exclude .svelte-kit/
  --exclude out/
)

if [ "$MODE" = "dry-run" ]; then
  rsync_args+=(--dry-run)
fi

echo "delete_remote_files=false"
echo "running rsync"

set +e
rsync "${rsync_args[@]}" "$SOURCE_DIR" "$TARGET"
rsync_code=$?
set -e

end_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

total_file_size="$(grep -E '^Total file size:' "$log_file" | tail -1 | sed 's/^Total file size:[[:space:]]*//' || true)"
transferred_file_size="$(grep -E '^Total transferred file size:' "$log_file" | tail -1 | sed 's/^Total transferred file size:[[:space:]]*//' || true)"
literal_data="$(grep -E '^Literal data:' "$log_file" | tail -1 | sed 's/^Literal data:[[:space:]]*//' || true)"
unmatched_data="$(grep -E '^Unmatched data:' "$log_file" | tail -1 | sed 's/^Unmatched data:[[:space:]]*//' || true)"
matched_data="$(grep -E '^Matched data:' "$log_file" | tail -1 | sed 's/^Matched data:[[:space:]]*//' || true)"
total_bytes_sent="$(grep -E '^(Total bytes sent|Total sent):' "$log_file" | tail -1 | sed -E 's/^(Total bytes sent|Total sent):[[:space:]]*//' || true)"
total_bytes_received="$(grep -E '^(Total bytes received|Total received):' "$log_file" | tail -1 | sed -E 's/^(Total bytes received|Total received):[[:space:]]*//' || true)"

status="ok"
if [ "$rsync_code" -ne 0 ]; then
  status="rsync_failed"
fi

{
  echo "{"
  echo "  \"mode\": \"$MODE\","
  echo "  \"status\": \"$status\","
  echo "  \"startedAt\": \"$start_iso\","
  echo "  \"endedAt\": \"$end_iso\","
  echo "  \"source\": \"$(printf '%s' "$SOURCE_DIR" | json_escape)\","
  echo "  \"target\": \"$(printf '%s' "$TARGET" | json_escape)\","
  echo "  \"deleteRemoteFiles\": false,"
  echo "  \"sshExitCode\": $ssh_code,"
  echo "  \"rsyncExitCode\": $rsync_code,"
  echo "  \"totalFileSize\": \"$(printf '%s' "$total_file_size" | json_escape)\","
  echo "  \"transferredFileSize\": \"$(printf '%s' "$transferred_file_size" | json_escape)\","
  echo "  \"literalData\": \"$(printf '%s' "$literal_data" | json_escape)\","
  echo "  \"unmatchedData\": \"$(printf '%s' "$unmatched_data" | json_escape)\","
  echo "  \"matchedData\": \"$(printf '%s' "$matched_data" | json_escape)\","
  echo "  \"totalBytesSent\": \"$(printf '%s' "$total_bytes_sent" | json_escape)\","
  echo "  \"totalBytesReceived\": \"$(printf '%s' "$total_bytes_received" | json_escape)\","
  echo "  \"logFile\": \"$(printf '%s' "$log_file" | json_escape)\""
  echo "}"
} > "$summary_file"

cp "$summary_file" "$LAST_RUN"

echo "ended_at=$end_iso"
echo "rsync_exit_code=$rsync_code"
echo "summary=$summary_file"
echo "log=$log_file"

exit "$rsync_code"
