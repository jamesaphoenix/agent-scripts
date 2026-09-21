# Local inference server

One resident model on the Mac Studio, served to coding agents over the tailnet
through llama.cpp's OpenAI-compatible API.

## Why one server rather than one model per agent

llama-server holds the weights once and multiplexes N slots over them with
continuous batching. Adding a worker costs its KV cache, not another 45GB of
weights. Separate agent roles do not need separate model copies.

## Hardware reality on this box

The Studio is an **M4 Max, not an Ultra**: 40 GPU cores, 128GB unified, and a
published 546 GB/s of memory bandwidth. That is roughly double a DGX Spark's
273 GB/s, so token generation should compare well. It has no equivalent of
CUDA batching for prefill, which is where the Spark's reported advantage comes
from, so judge it on your own agent traffic rather than on either vendor's
numbers.

## Models

| file | model | size | role |
|---|---|---|---|
| `Qwen3.8-27B-UD-Q4_K_M.gguf` | Qwen3.8-27B | 15.3 GB | **default**. Dense 27B, vision-capable, reasoning model. |
| `Ornith-1.5-35B-Q4_K_M.gguf` | Ornith-1.5-35B-A3B | 20.2 GB | Kept for comparison. 36B MoE, ~3B active, MIT. |

Chosen by compiling real Bevy 0.19 output with `cargo check` in a turns-to-green
loop (see `launchd-tasks/registry.json` for the numbers). Short version: with a
12-turn budget both solve everything and total wall time is a dead heat. Qwen
takes fewer, slower, very consistent turns; Ornith takes more, cheaper turns
with a better median but a worse tail. Qwen wins on predictability.

Switch models by env var and reinstall:

```bash
LLAMA_SERVE_MODEL=Ornith-1.5-35B-Q4_K_M.gguf \
LLAMA_SERVE_ALIAS=ornith-1.5-35b \
bash agent-loops/llama-server/install-launchd.sh
```

## Reasoning models

Qwen3.8-27B is a reasoning model. Left unbounded it will spend its entire token
budget thinking and return **nothing** - measured at 142.9s, 11,419 characters of
reasoning, zero characters of answer. The defaults installed here cap it:

```
LLAMA_SERVE_REASONING_BUDGET=512     # -1 unrestricted, 0 immediate end
LLAMA_SERVE_REASONING_EFFORT=low     # default|low|medium|high|xhigh|max
```

Two traps: `reasoning_effort=minimal` returns HTTP 500 in this build, and the
`/no_think` prompt prefix does **not** work - only these flags or
`chat_template_kwargs` do.

## Memory budgeting

128GB total, minus roughly 40GB the two Docker VMs are allowed and ~15GB for
macOS, leaves about 70GB. Coder-Next at Q4 is 45GB of weights plus the KV pool,
which is `--parallel` x `--kv-unified-per-slot`. The defaults (4 slots x 32k)
are sized to stay clear of the production stacks. Raising either costs memory
linearly, so raise one at a time and watch the compressor:

```bash
vm_stat | grep -E 'Pages free|compressor'
```

`iogpu.wired_limit_mb` is left at the system default (0, roughly 75% of RAM =
96GB). That is comfortably above the working set, so it needs no change. If a
much larger model is ever loaded, that is the knob to raise.

## Install

```bash
bash agent-loops/llama-server/install-launchd.sh
```

KeepAlive daemon, `RunAtLoad`, retrying every 30s. It deliberately exits
non-zero while the model file is absent so a download in flight does not
crash-loop noisily; it comes up on its own once the file lands.

## Using it

Reachable on the tailnet, so agents on the MacBook can point at it directly:

```bash
STUDIO=$(tailscale ip -4 -peer jamess-mac-studio 2>/dev/null || echo 100.77.197.1)
curl -s "http://$STUDIO:8080/health"
curl -s "http://$STUDIO:8080/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder-next","messages":[{"role":"user","content":"hi"}]}'
```

Anything that speaks the OpenAI API takes `http://<studio>:8080/v1` as its base
URL. Live slot state is at `/slots`, Prometheus metrics at `/metrics`.

## Security

Binds to the Tailscale address, not `0.0.0.0`, so it is not published to the
LAN. If Tailscale is down at boot it falls back to `127.0.0.1` rather than
`0.0.0.0`: failing closed to loopback beats silently exposing an
unauthenticated model server. Set `LLAMA_SERVE_API_KEY` if it ever leaves the
tailnet.

## Maintenance opt-out

```bash
touch state/llama-server/disabled && launchctl kill TERM gui/501/com.jud.llama-server
```

Frees the whole model from RAM. `rm` the file to resume.
