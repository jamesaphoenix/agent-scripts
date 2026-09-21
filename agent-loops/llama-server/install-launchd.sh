#!/usr/bin/env bash
# Install the local inference server as a KeepAlive LaunchAgent on THIS machine.
#
# Mac Studio only. Authoring happens on the MacBook per the root CLAUDE.md;
# this is the deploy step.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LABEL="${LLAMA_SERVE_LAUNCHD_LABEL:-com.jud.llama-server}"
STATE_DIR="${LLAMA_SERVE_STATE_DIR:-$ROOT_DIR/state/llama-server}"
MODEL_DIR="${LLAMA_SERVE_MODEL_DIR:-$HOME/models}"
MODEL_FILE="${LLAMA_SERVE_MODEL:-Qwen3-Coder-Next-Q4_K_M.gguf}"
MODEL_ALIAS="${LLAMA_SERVE_ALIAS:-qwen3-coder-next}"
PORT="${LLAMA_SERVE_PORT:-8080}"
PARALLEL="${LLAMA_SERVE_PARALLEL:-2}"
CTX_PER_SLOT="${LLAMA_SERVE_CTX_PER_SLOT:-131072}"
PLIST_DIR="$HOME/Library/LaunchAgents"
CACHE_TYPE_K="${LLAMA_SERVE_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${LLAMA_SERVE_CACHE_TYPE_V:-q8_0}"
FIT_TARGET_MIB="${LLAMA_SERVE_FIT_TARGET_MIB:-12288}"
SPEC_TYPE="${LLAMA_SERVE_SPEC_TYPE:-ngram-simple}"
REASONING_BUDGET="${LLAMA_SERVE_REASONING_BUDGET:--1}"
REASONING_EFFORT="${LLAMA_SERVE_REASONING_EFFORT:-default}"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"

for n in "$PORT" "$PARALLEL" "$CTX_PER_SLOT"; do
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "Expected integers for port/parallel/ctx, got '$n'."; exit 1; }
done

mkdir -p "$PLIST_DIR" "$STATE_DIR/logs"

cat >"$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT_DIR}/serve.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>${STATE_DIR}/logs/llama-server.out.log</string>
  <key>StandardErrorPath</key>
  <string>${STATE_DIR}/logs/llama-server.err.log</string>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LLAMA_SERVE_MODEL_DIR</key>
    <string>${MODEL_DIR}</string>
    <key>LLAMA_SERVE_MODEL</key>
    <string>${MODEL_FILE}</string>
    <key>LLAMA_SERVE_ALIAS</key>
    <string>${MODEL_ALIAS}</string>
    <key>LLAMA_SERVE_PORT</key>
    <string>${PORT}</string>
    <key>LLAMA_SERVE_PARALLEL</key>
    <string>${PARALLEL}</string>
    <key>LLAMA_SERVE_CTX_PER_SLOT</key>
    <string>${CTX_PER_SLOT}</string>
    <key>LLAMA_SERVE_CACHE_TYPE_K</key>
    <string>${CACHE_TYPE_K}</string>
    <key>LLAMA_SERVE_CACHE_TYPE_V</key>
    <string>${CACHE_TYPE_V}</string>
    <key>LLAMA_SERVE_FIT_TARGET_MIB</key>
    <string>${FIT_TARGET_MIB}</string>
    <key>LLAMA_SERVE_SPEC_TYPE</key>
    <string>${SPEC_TYPE}</string>
    <key>LLAMA_SERVE_REASONING_BUDGET</key>
    <string>${REASONING_BUDGET}</string>
    <key>LLAMA_SERVE_REASONING_EFFORT</key>
    <string>${REASONING_EFFORT}</string>
    <key>LLAMA_SERVE_DISABLE_FILE</key>
    <string>${STATE_DIR}/disabled</string>
  </dict>
</dict>
</plist>
PLIST

plutil -lint "$PLIST_PATH"

uid="$(id -u)"
launchctl bootout "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true

# bootout is ASYNCHRONOUS. Tearing down a job holding a 20GB+ model takes
# several seconds, and bootstrap run against a still-terminating job fails with
# "Operation already in progress" - leaving NOTHING loaded while the plist on
# disk looks correct. That silent failure bit three model switches before it was
# spotted, so wait for the job to actually disappear, then verify bootstrap.
for _ in $(seq 1 90); do
  launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1 || break
  sleep 1
done
if launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  echo "ERROR: ${LABEL} still loaded after 90s; refusing to bootstrap over it." >&2
  exit 1
fi

if ! launchctl bootstrap "gui/${uid}" "$PLIST_PATH"; then
  echo "ERROR: launchctl bootstrap failed for ${LABEL}." >&2
  exit 1
fi
launchctl enable "gui/${uid}/${LABEL}" >/dev/null 2>&1 || true

# Confirm it is actually loaded rather than trusting the exit code.
if ! launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  echo "ERROR: ${LABEL} is not loaded after bootstrap." >&2
  exit 1
fi

cat <<EOF
Installed ${LABEL}
Plist: ${PLIST_PATH}
Model: ${MODEL_DIR}/${MODEL_FILE}  (alias: ${MODEL_ALIAS})
Slots: ${PARALLEL} x ${CTX_PER_SLOT} ctx (kv ${CACHE_TYPE_K}/${CACHE_TYPE_V})
Logs:  ${STATE_DIR}/logs/
Pause: touch ${STATE_DIR}/disabled && launchctl kill TERM gui/${uid}/${LABEL}
Resume: rm ${STATE_DIR}/disabled

ProcessType=Interactive keeps macOS from throttling it as a background task.
KeepAlive means it also retries every ${LLAMA_SERVE_THROTTLE:-30}s while the model
file is still downloading, then comes up on its own once the file lands.

Inspect:
  launchctl print "gui/${uid}/${LABEL}"
Health:
  curl -s http://\$(tailscale ip -4 | head -1):${PORT}/health
EOF
