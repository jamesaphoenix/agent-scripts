#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TEST_DIR="$(mktemp -d -t playwright-reaper-test.XXXXXX)"
trap 'rm -rf "$TEST_DIR"' EXIT

FIXTURE="$TEST_DIR/processes.txt"
STALE_PROFILE="$TEST_DIR/playwright_chromiumdev_profile-stale"
mkdir -p "$STALE_PROFILE"
touch -t 202001010000 "$STALE_PROFILE"

cat >"$FIXTURE" <<EOF
101 1 /opt/homebrew/bin/node /pkg/playwright-core/lib/entry/cliDaemon.js --daemon-session=/Users/test/Library/Caches/ms-playwright/daemon/key/live.session
102 101 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=$TEST_DIR/playwright_chromiumdev_profile-live
103 1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=$TEST_DIR/playwright_chromiumdev_profile-orphan
104 103 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --user-data-dir=$TEST_DIR/playwright_chromiumdev_profile-orphan
105 1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --profile-directory=Default
EOF

default_output="$(
  PLAYWRIGHT_REAPER_PS_FILE="$FIXTURE" \
  PLAYWRIGHT_REAPER_TMP_ROOT="$TEST_DIR" \
  PLAYWRIGHT_REAPER_MIN_AGE_MINUTES=0 \
    "$SCRIPT_DIR/reap.sh" --dry-run
)"

grep -F "would terminate: pid=103 ppid=1 kind=browser" <<<"$default_output" >/dev/null
if grep -F "pid=101" <<<"$default_output" >/dev/null; then
  echo "Default mode selected a live daemon." >&2
  exit 1
fi
if grep -F "pid=102" <<<"$default_output" >/dev/null; then
  echo "Default mode selected a browser with a live daemon parent." >&2
  exit 1
fi
if grep -F "pid=105" <<<"$default_output" >/dev/null; then
  echo "Default mode selected a normal Chrome process." >&2
  exit 1
fi
grep -F "would remove stale temp profile: $STALE_PROFILE" <<<"$default_output" >/dev/null

force_output="$(
  PLAYWRIGHT_REAPER_PS_FILE="$FIXTURE" \
  PLAYWRIGHT_REAPER_TMP_ROOT="$TEST_DIR" \
  PLAYWRIGHT_REAPER_MIN_AGE_MINUTES=0 \
    "$SCRIPT_DIR/reap.sh" --force --dry-run
)"

for pid in 101 102 103 104; do
  grep -F "pid=${pid} " <<<"$force_output" >/dev/null
done
if grep -F "pid=105" <<<"$force_output" >/dev/null; then
  echo "Force mode selected a normal Chrome process." >&2
  exit 1
fi

status_output="$(PLAYWRIGHT_REAPER_PS_FILE="$FIXTURE" "$SCRIPT_DIR/reap.sh" --status)"
grep -F "playwright_cli_daemons=1" <<<"$status_output" >/dev/null
grep -F "playwright_browser_processes=3" <<<"$status_output" >/dev/null
grep -F "orphan_browser_processes=1" <<<"$status_output" >/dev/null

echo "playwright-cli-reaper tests passed"
