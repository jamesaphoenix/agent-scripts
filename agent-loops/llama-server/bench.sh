#!/usr/bin/env bash
# Concurrency + prompt-cache benchmark for the local inference server.
#
# The number that matters for an agent factory is not single-stream tok/s. An
# agent loop re-reads instructions, files and tool output every turn, so it is
# dominated by prefill, and several workers share one server. This measures
# both: aggregate throughput at 1/2/4 concurrent requests, and the difference
# between a cold prompt and a repeated prefix (which --cache-reuse should make
# much cheaper).
#
# Usage: bench.sh [host] [port] [model-alias]

set -uo pipefail

HOST="${1:-${LLAMA_BENCH_HOST:-100.77.197.1}}"
PORT="${2:-${LLAMA_BENCH_PORT:-8080}}"
MODEL="${3:-${LLAMA_BENCH_MODEL:-qwen3-coder-next}}"
BASE="http://${HOST}:${PORT}"
MAX_TOKENS="${LLAMA_BENCH_MAX_TOKENS:-256}"

command -v curl >/dev/null || { echo "curl required"; exit 1; }
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

curl -s -m 5 "$BASE/health" >/dev/null || { echo "server not healthy at $BASE"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A prefix long enough to be worth caching, standing in for the system prompt +
# repo context an agent resends every turn.
build_prompt() {
  local unique="$1" filler
  filler="$(python3 -c "print(('You are a senior engineer reviewing a TypeScript service. ' * 120))")"
  python3 - "$filler" "$unique" <<'PY'
import json, sys
filler, unique = sys.argv[1], sys.argv[2]
print(json.dumps(f"{filler}\n\nTask {unique}: write one concise function and stop."))
PY
}

one_request() {
  local prompt_json="$1" out="$2"
  local body
  body="$(python3 - "$prompt_json" "$MODEL" "$MAX_TOKENS" <<'PY'
import json, sys
prompt, model, mx = sys.argv[1], sys.argv[2], int(sys.argv[3])
print(json.dumps({"model": model,
                  "messages": [{"role": "user", "content": json.loads(prompt)}],
                  "max_tokens": mx, "stream": False, "temperature": 0}))
PY
)"
  curl -s -m 600 "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d "$body" > "$out"
}

summarise() {
  local label="$1" elapsed="$2"; shift 2
  python3 - "$label" "$elapsed" "$@" <<'PY'
import json, sys
label, elapsed = sys.argv[1], float(sys.argv[2])
files = sys.argv[3:]
pt = ct = 0; ok = 0
for f in files:
    try:
        d = json.load(open(f))
        u = d.get("usage") or {}
        pt += u.get("prompt_tokens") or 0
        ct += u.get("completion_tokens") or 0
        ok += 1
    except Exception:
        pass
print(f"  {label:<28} {ok} req  {elapsed:6.1f}s  "
      f"prompt={pt:>6}  gen={ct:>5}  "
      f"agg_gen={ct/elapsed if elapsed else 0:6.1f} tok/s  "
      f"agg_total={(pt+ct)/elapsed if elapsed else 0:7.1f} tok/s")
PY
}

run_concurrency() {
  local n="$1" tag="$2" shared="$3"
  local files=() pids=()
  for i in $(seq 1 "$n"); do
    local uniq
    if [[ "$shared" == "shared" ]]; then uniq="$i"; else uniq="$RANDOM-$i"; fi
    build_prompt "$uniq" > "$TMP/p.$tag.$i"
    files+=("$TMP/r.$tag.$i")
  done
  local start end
  start="$(python3 -c 'import time;print(time.time())')"
  for i in $(seq 1 "$n"); do
    one_request "$(cat "$TMP/p.$tag.$i")" "$TMP/r.$tag.$i" &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p"; done
  end="$(python3 -c 'import time;print(time.time())')"
  summarise "$tag" "$(python3 -c "print($end-$start)")" "${files[@]}"
}

echo "llama-server benchmark  ($BASE, model=$MODEL, max_tokens=$MAX_TOKENS)"
echo

echo "cold prefix (each request a different long prompt):"
run_concurrency 1 "concurrency-1-cold" fresh
run_concurrency 2 "concurrency-2-cold" fresh
run_concurrency 4 "concurrency-4-cold" fresh

echo
echo "shared prefix (same long prefix, exercises --cache-reuse):"
run_concurrency 1 "concurrency-1-warm" shared
run_concurrency 4 "concurrency-4-warm" shared

echo
echo "Read agg_gen for how much total generation the box sustains, and compare"
echo "cold vs warm at the same concurrency for what prompt caching is buying."
