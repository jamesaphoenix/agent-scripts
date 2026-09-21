#!/bin/bash
# Prune leaked Chrome code_sign_clone dirs in the per-user temp folder.
#
# Chrome on macOS APFS-clones its own .app bundle at launch so the updater can
# replace the original while it runs, then removes the clone on clean exit.
# Browsers that are force-killed or orphaned (playwright-cli daemons, crashed
# automation) never clean up, and each leaked clone costs ~1.4GB.
#
# Safety: only deletes clone dirs that no running process holds open.

set -uo pipefail

# Clones live in the per-user "X" folder (sibling of the T/ temp dir), which has
# no getconf constant - derive it from DARWIN_USER_TEMP_DIR's parent.
CLONE_DIR="$(dirname "$(getconf DARWIN_USER_TEMP_DIR)")/X/com.google.Chrome.code_sign_clone"
LOG="${HOME}/Library/Logs/chrome-clone-pruner.log"
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" >>"$LOG"; }

[ -d "$CLONE_DIR" ] || exit 0

# Clone dirs currently held open by any running process - never touch these.
# A bare `lsof` is unreliable here (it can stall on other mounts and return
# nothing), and an empty result would look like "all orphaned" and delete the
# clone the running Chrome is executing from. Scope it to the clone dir and
# treat a failed probe as fatal rather than as "nothing in use".
probe="$(mktemp)"
trap 'rm -f "$probe"' EXIT
# No coreutils `timeout` under launchd's minimal PATH, so watchdog it by hand.
# `lsof +D` exits non-zero whenever it cannot stat some file, which is routine
# here, so its status says nothing useful - only the timeout is fatal.
/usr/sbin/lsof +D "$CLONE_DIR" >"$probe" 2>/dev/null &
probe_pid=$!
finished=""
for _ in $(seq 1 120); do
  if ! kill -0 "$probe_pid" 2>/dev/null; then
    wait "$probe_pid" 2>/dev/null
    finished=1
    break
  fi
  sleep 1
done
if [ -z "$finished" ]; then
  kill -9 "$probe_pid" 2>/dev/null
  wait "$probe_pid" 2>/dev/null
  log "lsof probe timed out; skipping this run"
  exit 0
fi

IN_USE="$(grep -o 'code_sign_clone\.[A-Za-z0-9]*' "$probe" | sort -u)"

# Sanity invariant: a running Chrome executes out of its own clone, so if any
# Chrome is alive the probe must name at least one in-use clone. If it names
# none, the probe is not to be trusted - bail rather than delete a live bundle.
if /usr/bin/pgrep -f "Google Chrome.app/Contents/MacOS/Google Chrome" >/dev/null 2>&1 \
   && [ -z "$IN_USE" ]; then
  log "Chrome is running but probe found no in-use clone; skipping this run"
  exit 0
fi

removed=0
for d in "$CLONE_DIR"/code_sign_clone.*; do
  [ -d "$d" ] || continue
  name="$(basename "$d")"
  if printf '%s\n' "$IN_USE" | grep -qx "$name"; then
    continue
  fi
  # Belt and braces: leave anything touched in the last hour alone, in case a
  # browser is mid-launch and has not opened its bundle yet.
  if [ -n "$(find "$d" -maxdepth 0 -mmin -60)" ]; then
    continue
  fi
  rm -rf "$d" 2>/dev/null && removed=$((removed + 1))
done

[ "$removed" -gt 0 ] && log "pruned ${removed} orphaned clone dir(s); $(df -h /System/Volumes/Data | awk 'NR==2{print $4}') free"
exit 0
