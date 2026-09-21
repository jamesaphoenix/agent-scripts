#!/usr/bin/env bash
# Local inference server (llama.cpp) for coding agents.
#
# One resident model serving several agents through continuous batching.
# Separate agent roles do NOT need separate copies of the model: llama-server
# holds the weights once and multiplexes N slots over them, so the marginal
# cost of another worker is its KV cache, not another 45GB of weights.
#
# Binds to the Tailscale address by default rather than 0.0.0.0, so the API is
# reachable from the MacBook (and any other tailnet device) without also being
# exposed to everything on the LAN. llama-server has no auth by default; set
# LLAMA_SERVE_API_KEY if you ever move it off the tailnet.

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

MODEL_DIR="${LLAMA_SERVE_MODEL_DIR:-$HOME/models}"
MODEL_FILE="${LLAMA_SERVE_MODEL:-Qwen3-Coder-Next-Q4_K_M.gguf}"
MODEL_ALIAS="${LLAMA_SERVE_ALIAS:-qwen3-coder-next}"
PORT="${LLAMA_SERVE_PORT:-8080}"
# Server slots. Each slot is one concurrent request, not one agent: agents that
# are thinking rather than generating are not holding a slot.
PARALLEL="${LLAMA_SERVE_PARALLEL:-4}"
# Context per slot. The KV pool is sized n_parallel * this, so raising it costs
# memory linearly in the number of slots.
CTX_PER_SLOT="${LLAMA_SERVE_CTX_PER_SLOT:-32768}"
GPU_LAYERS="${LLAMA_SERVE_GPU_LAYERS:-999}"
# Minimum chunk the server will try to salvage from a previous prompt via KV
# shifting. This is what makes an agent's second turn cheap: the shared
# instruction/repo prefix is not re-prefilled.
CACHE_REUSE="${LLAMA_SERVE_CACHE_REUSE:-256}"
# KV cache dtype. Measured on this box with Qwen3-Coder-Next (llama-bench,
# pp4096/tg128):
#
#   f16 /f16   927 t/s prefill, 70.6 t/s decode   <- default
#   q8_0/q8_0  924 t/s prefill, 68.2 t/s decode   <- same speed, half the memory
#   f16 /q8_0  267 t/s prefill, 52.3 t/s decode   <- do not mix
#   q8_0/f16   529 t/s prefill, 57.3 t/s decode   <- do not mix
#
# So quantising the KV cache buys memory, not speed, and that memory is what
# pays for longer per-slot context. Keep K and V the SAME type: a mismatched
# pair costs up to 71% of prefill throughput.
CACHE_TYPE_K="${LLAMA_SERVE_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${LLAMA_SERVE_CACHE_TYPE_V:-q8_0}"
# --fit is on by default in llama.cpp and auto-sizes UNSET arguments to device
# memory, leaving --fit-target MiB spare. The default margin (1024 MiB) is far
# too thin on a box that is also running the production Docker stacks, so widen
# it: this is the one autotuning knob that actually matters here.
FIT_TARGET_MIB="${LLAMA_SERVE_FIT_TARGET_MIB:-12288}"
# Speculative decoding. `ngram-*` needs no draft model: it predicts from n-grams
# already present in the context, which is exactly the shape of code editing,
# where the output largely echoes the input. Measured A/B on this box
# (Qwen3-Coder-Next, median of 3, temperature 0):
#
#                       code edit    prose
#   none                 69.0 t/s    67.6 t/s
#   ngram-simple         89.9 t/s    63.6 t/s   <- +30% coding, -6% prose
#
# Net win for a coding agent. Do NOT use `ngram-cache`: it measured faster
# still (~104 t/s) but crashes on some prompts with
# "llama_decode: failed to decode, ret = -3" -> HTTP 500, so it is not safe.
SPEC_TYPE="${LLAMA_SERVE_SPEC_TYPE:-ngram-simple}"
# Reasoning controls. Only meaningful for reasoning models (Qwen3.8-27B emits a
# separate reasoning_content channel); harmless for Qwen3-Coder-Next, which is
# not one, so they are safe to leave set while switching models.
#
# Left unset, a reasoning model will happily spend its entire token budget
# thinking and return NOTHING. Measured on Qwen3.8-27B with the same Bevy task:
#
#   unbounded            142.9s  11419 chars reasoning,   0 chars answer (!)
#   budget=512 default    28.5s   2102 chars reasoning, 293 chars answer
#   budget=512 low        16.1s    789 chars reasoning, 408 chars answer  <- best
#   budget=512 medium     16.3s    817 chars reasoning, 386 chars answer
#   effort=minimal        HTTP 500 (bug in this build - do not use)
#
# -1 = unrestricted, 0 = end thinking immediately, N > 0 = cap at N tokens.
REASONING_BUDGET="${LLAMA_SERVE_REASONING_BUDGET:--1}"
# default | low | medium | high | xhigh | max  ('minimal' 500s, avoid)
REASONING_EFFORT="${LLAMA_SERVE_REASONING_EFFORT:-default}"
DISABLE_FILE="${LLAMA_SERVE_DISABLE_FILE:-}"

if [[ -n "$DISABLE_FILE" && -e "$DISABLE_FILE" ]]; then
  echo "Disabled by $DISABLE_FILE - not starting."
  exit 0
fi

MODEL_PATH="$MODEL_DIR/$MODEL_FILE"
if [[ ! -f "$MODEL_PATH" ]]; then
  # Downloads run out of band. Exit non-zero so launchd's KeepAlive retries on
  # its ThrottleInterval instead of us busy-waiting here holding a slot open.
  echo "Model not present yet: $MODEL_PATH"
  exit 1
fi

command -v llama-server >/dev/null 2>&1 || {
  echo "llama-server not found (brew install llama.cpp)"
  exit 1
}

# Prefer the tailnet address. Fall back to loopback rather than 0.0.0.0: if
# Tailscale is not up yet, failing closed to localhost is safer than silently
# publishing an unauthenticated model server to the LAN.
resolve_host() {
  if [[ -n "${LLAMA_SERVE_HOST:-}" ]]; then
    printf '%s' "$LLAMA_SERVE_HOST"
    return 0
  fi
  local ts
  ts="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  if [[ -z "$ts" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
    ts="$(/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4 2>/dev/null | head -1 || true)"
  fi
  if [[ -n "$ts" ]]; then
    printf '%s' "$ts"
  else
    printf '%s' "127.0.0.1"
  fi
}

HOST="$(resolve_host)"

args=(
  --model "$MODEL_PATH"
  --alias "$MODEL_ALIAS"
  --host "$HOST"
  --port "$PORT"
  --parallel "$PARALLEL"
  --kv-unified-per-slot "$CTX_PER_SLOT"
  --n-gpu-layers "$GPU_LAYERS"
  --flash-attn on
  --cont-batching
  --cache-reuse "$CACHE_REUSE"
  --cache-type-k "$CACHE_TYPE_K"
  --cache-type-v "$CACHE_TYPE_V"
  --fit-target "$FIT_TARGET_MIB"
  --metrics
  --jinja
  --no-webui
)

if [[ -n "$SPEC_TYPE" && "$SPEC_TYPE" != "none" ]]; then
  args+=(--spec-type "$SPEC_TYPE")
fi

if [[ "$REASONING_BUDGET" != "-1" ]]; then
  args+=(--reasoning-budget "$REASONING_BUDGET")
  # Without this the model just stops mid-thought; the nudge gets it to commit
  # to an answer with the tokens it has left.
  args+=(--reasoning-budget-message "Time to answer. Stop reasoning and give the code now.")
fi

if [[ "$REASONING_EFFORT" != "default" ]]; then
  args+=(--reasoning-effort "$REASONING_EFFORT")
fi

if [[ -n "${LLAMA_SERVE_API_KEY:-}" ]]; then
  args+=(--api-key "$LLAMA_SERVE_API_KEY")
fi

echo "Starting llama-server"
echo "  model:    $MODEL_PATH"
echo "  bind:     http://$HOST:$PORT  (OpenAI-compatible at /v1)"
echo "  slots:    $PARALLEL x ${CTX_PER_SLOT} ctx  (kv ${CACHE_TYPE_K}/${CACHE_TYPE_V}, fit margin ${FIT_TARGET_MIB}MiB, spec ${SPEC_TYPE}, reason ${REASONING_EFFORT}/${REASONING_BUDGET})"
exec llama-server "${args[@]}"
