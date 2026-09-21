#!/bin/bash
# Prune old kit-fuzz render output from the just-understanding-data checkouts.
#
# The continuous kit hardening agent renders about 20 films a pass and keeps
# the frames of any run it wants to look at again (inspect.mjs --keep, replays,
# presentation bundles). That is roughly 3GB a day of gitignored PNGs under
# front_end/reports/kit-fuzz/<timestamp>-*/ and nothing ever removed it.
#
# What this touches: ONLY timestamped run dirs (2026-09-18T18-05-49-055-20,
# ...-presentations), which .gitignore already excludes. Two tiers by the age
# in the dir's own name:
#   older than MEDIA_HOURS  delete everything except *.json and *.mp4 (plans,
#                           frames.json and motion.json stay, so a seed can be
#                           replayed, and the finished films are small)
#   older than DIR_HOURS    delete the whole dir, as inspect.mjs does by default
# When the data volume has less than LOW_FREE_GB free, both ages drop to the
# PRESSURE_* values, and below ALERT_FREE_GB a notification is posted.
#
# What this never touches:
#   - top-level reports (<stamp>.md, <stamp>.json, coverage.json, ledgers): they
#     are tracked in git and are the output the run acts on
#   - named dirs (camera-point-before, 3d, video, ...): deliberate proofs
#   - any run dir named in tools/kit-fuzz, reports/kit-hardening or
#     vision-calibration.md: the scorecard and the checks cite them as evidence
#   - any run dir with a file written in the last ACTIVE_HOURS: the agent
#     replays seeds inside older run dirs
#
#   DRY_RUN=1 bash prune-kit-fuzz.sh     print what would go, delete nothing
#   touch state/kit-fuzz-pruner/disabled  maintenance opt-out

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_DIR="${KIT_FUZZ_PRUNER_STATE_DIR:-$ROOT_DIR/state/kit-fuzz-pruner}"
REPO="${KIT_FUZZ_PRUNER_REPO:-$HOME/Desktop/projects/just-understanding-data/just-understanding-data}"
LOG="${HOME}/Library/Logs/kit-fuzz-pruner.log"

MEDIA_HOURS="${KIT_FUZZ_PRUNER_MEDIA_HOURS:-24}"
DIR_HOURS="${KIT_FUZZ_PRUNER_DIR_HOURS:-72}"
ACTIVE_HOURS="${KIT_FUZZ_PRUNER_ACTIVE_HOURS:-6}"
LOW_FREE_GB="${KIT_FUZZ_PRUNER_LOW_FREE_GB:-40}"
PRESSURE_MEDIA_HOURS="${KIT_FUZZ_PRUNER_PRESSURE_MEDIA_HOURS:-6}"
PRESSURE_DIR_HOURS="${KIT_FUZZ_PRUNER_PRESSURE_DIR_HOURS:-24}"
ALERT_FREE_GB="${KIT_FUZZ_PRUNER_ALERT_FREE_GB:-20}"
DRY_RUN="${DRY_RUN:-}"

mkdir -p "$(dirname "$LOG")" "$STATE_DIR"
log() {
  local line
  line="[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"
  if [ -n "$DRY_RUN" ]; then echo "$line"; else echo "$line" >>"$LOG"; fi
}

[ -e "$STATE_DIR/disabled" ] && exit 0
[ -d "$REPO" ] || exit 0

free_gb() { df -g /System/Volumes/Data | awk 'NR==2 {print $4}'; }

free_before="$(free_gb)"
pressure=""
if [ "$free_before" -lt "$LOW_FREE_GB" ]; then
  pressure=1
  MEDIA_HOURS="$PRESSURE_MEDIA_HOURS"
  DIR_HOURS="$PRESSURE_DIR_HOURS"
  # Never prune something the agent may still be writing.
  [ "$ACTIVE_HOURS" -gt "$MEDIA_HOURS" ] && ACTIVE_HOURS="$MEDIA_HOURS"
fi

now="$(date +%s)"
cites="$(mktemp)"
trap 'rm -f "$cites"' EXIT

media_pruned=0
dirs_removed=0
kept_cited=0
kept_active=0

# Age in hours from the stamp in the dir's name, which is UTC (toISOString).
# The mtime is no use: deleting the media would make the dir look new again.
age_hours() {
  local stamp epoch
  stamp="${1:0:19}"
  epoch="$(date -j -u -f '%Y-%m-%dT%H-%M-%S' "$stamp" +%s 2>/dev/null)" || return 1
  echo $(( (now - epoch) / 3600 ))
}

prune_checkout() {
  local front_end="$1" fuzz d name age
  fuzz="$front_end/reports/kit-fuzz"
  [ -d "$fuzz" ] || return 0

  # Every run dir this checkout's scorecard, journal, checkpoints or tool
  # sources name. A dir is protected when its name appears anywhere in here.
  : >"$cites"
  grep -rhoE '20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}[A-Za-z0-9-]*' \
    "$front_end/tools/kit-fuzz" "$front_end/reports/kit-hardening" \
    "$fuzz/vision-calibration.md" 2>/dev/null | sort -u >>"$cites"

  for d in "$fuzz"/20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]-*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    name="$(basename "$d")"
    age="$(age_hours "$name")" || continue
    [ "$age" -ge "$MEDIA_HOURS" ] || continue

    if grep -qF -- "$name" "$cites"; then
      kept_cited=$((kept_cited + 1))
      continue
    fi
    if [ -n "$(find "$d" -type f -mmin "-$((ACTIVE_HOURS * 60))" -print -quit 2>/dev/null)" ]; then
      kept_active=$((kept_active + 1))
      continue
    fi

    if [ "$age" -ge "$DIR_HOURS" ]; then
      if [ -n "$DRY_RUN" ]; then
        log "would remove ${d#"$REPO"/} (${age}h, $(du -sh "$d" | awk '{print $1}'))"
      else
        rm -rf "$d" 2>/dev/null
      fi
      dirs_removed=$((dirs_removed + 1))
    elif [ -n "$(find "$d" -type f ! -name '*.json' ! -name '*.mp4' -print -quit 2>/dev/null)" ]; then
      if [ -n "$DRY_RUN" ]; then
        log "would strip media from ${d#"$REPO"/} (${age}h)"
      else
        find "$d" -type f ! -name '*.json' ! -name '*.mp4' -delete 2>/dev/null
        find "$d" -mindepth 1 -type d -empty -delete 2>/dev/null
      fi
      media_pruned=$((media_pruned + 1))
    fi
  done
}

prune_checkout "$REPO/front_end"
for wt in "$REPO"/.claude/worktrees/*/front_end "$REPO"/.worktrees/*/front_end; do
  [ -d "$wt" ] && prune_checkout "$wt"
done

free_after="$(free_gb)"
if [ $((media_pruned + dirs_removed)) -gt 0 ] || [ -n "$pressure" ]; then
  log "${DRY_RUN:+DRY RUN: }removed ${dirs_removed} run dir(s), stripped media from ${media_pruned}; kept ${kept_cited} cited, ${kept_active} active; tiers ${MEDIA_HOURS}h/${DIR_HOURS}h${pressure:+ (low disk)}; free ${free_before}GB -> ${free_after}GB"
fi

if [ -z "$DRY_RUN" ] && [ "$free_after" -lt "$ALERT_FREE_GB" ]; then
  # At most one notification every 6 hours.
  last="$(cat "$STATE_DIR/last-alert-epoch" 2>/dev/null || echo 0)"
  if [ $((now - last)) -ge 21600 ]; then
    /usr/bin/osascript -e "display notification \"Only ${free_after}GB free after pruning kit-fuzz output. Something else is filling the disk.\" with title \"Disk space low\"" 2>/dev/null
    echo "$now" >"$STATE_DIR/last-alert-epoch"
    log "ALERT: ${free_after}GB free, below ${ALERT_FREE_GB}GB"
  fi
fi
exit 0
