#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Ralph Loop — Unified Agent Harness (Claude Code + Codex)
# ============================================================================
# Based on Geoffrey Huntley's RALPH technique: https://ghuntley.com/ralph
# Anthropic's effective harnesses: https://anthropic.com/engineering/effective-harnesses-for-long-running-agents
#
# Runs a prompt in a loop, spawning fresh agent instances each iteration.
# Supports both Claude Code (headless) and OpenAI Codex as engines.
#
# Usage:
#   ./ralph.sh                              # Auto-detect engine, use prompt.md
#   ./ralph.sh --engine claude              # Force Claude Code
#   ./ralph.sh --engine codex               # Force Codex
#   ./ralph.sh --prompt my-task.md          # Custom prompt file
#   ./ralph.sh --cooldown 10                # 10s between iterations
#   ./ralph.sh --max-iterations 5           # Stop after 5 iterations
#   ./ralph.sh --max-hours 8               # Stop after 8 hours
#   ./ralph.sh --system-prompt sys.md       # Claude: custom system prompt
#   ./ralph.sh --model o4-mini              # Codex: model override
#   ./ralph.sh --codex-profile my-profile   # Codex: named profile
#   ./ralph.sh --no-stream                  # Claude: plain output (no stream-json UI)
#   ./ralph.sh --no-mcp                     # Codex: disable remote MCP servers
#   ./ralph.sh --dangerous                  # Codex: bypass sandbox entirely
#   ./ralph.sh --dry-run                    # Show config and exit
#   ./ralph.sh --help                       # Show this help
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Defaults ---------------------------------------------------------------

PROMPT_FILE="${PROMPT_FILE:-${SCRIPT_DIR}/prompt.md}"
COOLDOWN="${COOLDOWN:-5}"
MAX_ITERATIONS="${MAX_ITERATIONS:-0}"   # 0 = infinite
MAX_HOURS="${MAX_HOURS:-0}"             # 0 = infinite
ENGINE="${ENGINE:-auto}"
SYSTEM_PROMPT_FILE="${SYSTEM_PROMPT_FILE:-}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_PROFILE="${CODEX_PROFILE:-}"
STREAM_UI="${STREAM_UI:-true}"
NO_MCP="${NO_MCP:-false}"
DANGEROUS="${DANGEROUS:-false}"
DRY_RUN=false
LOG_DIR="${LOG_DIR:-/tmp/ralph-loop-logs}"

# ---- Parse flags ------------------------------------------------------------

show_help() {
  cat <<'HELP'
Ralph Loop — Unified Agent Harness (Claude Code + Codex)

USAGE:
  ./ralph.sh [OPTIONS]

OPTIONS:
  --engine <claude|codex|auto>   Agent engine (default: auto-detect)
  --prompt <file>                Prompt file (default: ./prompt.md)
  --cooldown <seconds>           Pause between iterations (default: 5)
  --max-iterations <n>           Stop after N iterations (0 = infinite)
  --max-hours <n>                Stop after N hours (0 = infinite)

  Claude-specific:
  --system-prompt <file>         System prompt file (passed via --system-prompt)
  --no-stream                    Disable stream-json UI (plain --print output)

  Codex-specific:
  --model <model>                Model override (e.g., o4-mini, o3)
  --codex-profile <name>         Codex profile name
  --no-mcp                       Disable remote MCP servers
  --dangerous                    Use --dangerously-bypass-approvals-and-sandbox

  General:
  --dry-run                      Show resolved config and exit
  --help                         Show this help

ENVIRONMENT VARIABLES:
  PROMPT_FILE, COOLDOWN, MAX_ITERATIONS, MAX_HOURS, ENGINE,
  SYSTEM_PROMPT_FILE, CODEX_MODEL, CODEX_PROFILE, LOG_DIR,
  STREAM_UI, NO_MCP, DANGEROUS

EXAMPLES:
  # Auto-detect, run with default prompt.md
  ./ralph.sh

  # Codex with custom model, 3 iterations
  ./ralph.sh --engine codex --model o4-mini --max-iterations 3

  # Claude with system prompt, 8-hour overnight run
  ./ralph.sh --engine claude --system-prompt system.md --max-hours 8
HELP
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --engine)         ENGINE="$2"; shift 2 ;;
    --prompt)         PROMPT_FILE="$2"; shift 2 ;;
    --cooldown)       COOLDOWN="$2"; shift 2 ;;
    --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
    --max-hours)      MAX_HOURS="$2"; shift 2 ;;
    --system-prompt)  SYSTEM_PROMPT_FILE="$2"; shift 2 ;;
    --model)          CODEX_MODEL="$2"; shift 2 ;;
    --codex-profile)  CODEX_PROFILE="$2"; shift 2 ;;
    --no-stream)      STREAM_UI=false; shift ;;
    --no-mcp)         NO_MCP=true; shift ;;
    --dangerous)      DANGEROUS=true; shift ;;
    --dry-run)        DRY_RUN=true; shift ;;
    --help|-h)        show_help ;;
    *) echo "Unknown flag: $1 (try --help)"; exit 1 ;;
  esac
done

# ---- Colours ----------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ---- Engine detection -------------------------------------------------------

detect_engine() {
  if [[ "$ENGINE" != "auto" ]]; then
    echo "$ENGINE"
    return
  fi
  if command -v claude &>/dev/null; then
    echo "claude"
  elif command -v codex &>/dev/null; then
    echo "codex"
  else
    echo ""
  fi
}

ENGINE=$(detect_engine)

if [[ -z "$ENGINE" ]]; then
  echo -e "${RED}Error: Neither 'claude' nor 'codex' found in PATH.${NC}" >&2
  echo "Install one of:" >&2
  echo "  Claude Code: brew install claude-code" >&2
  echo "  Codex:       npm install -g @openai/codex" >&2
  exit 1
fi

if [[ "$ENGINE" != "claude" && "$ENGINE" != "codex" ]]; then
  echo -e "${RED}Error: Unknown engine '${ENGINE}'. Use 'claude', 'codex', or 'auto'.${NC}" >&2
  exit 1
fi

# ---- Dependency checks ------------------------------------------------------

check_deps() {
  local missing=0
  if ! command -v jq &>/dev/null; then
    echo -e "${RED}Error: jq is required (brew install jq)${NC}" >&2
    missing=1
  fi
  if [[ "$ENGINE" == "claude" ]] && ! command -v claude &>/dev/null; then
    echo -e "${RED}Error: 'claude' not found in PATH${NC}" >&2
    missing=1
  fi
  if [[ "$ENGINE" == "codex" ]] && ! command -v codex &>/dev/null; then
    echo -e "${RED}Error: 'codex' not found in PATH${NC}" >&2
    missing=1
  fi
  if [[ ! -f "$PROMPT_FILE" ]]; then
    echo -e "${RED}Error: Prompt file not found: ${PROMPT_FILE}${NC}" >&2
    missing=1
  fi
  if [[ -n "$SYSTEM_PROMPT_FILE" && ! -f "$SYSTEM_PROMPT_FILE" ]]; then
    echo -e "${RED}Error: System prompt file not found: ${SYSTEM_PROMPT_FILE}${NC}" >&2
    missing=1
  fi
  if [[ $missing -eq 1 ]]; then
    exit 1
  fi
}

check_deps

# ---- Time management --------------------------------------------------------

START_TIME=$(date +%s)

if [[ "$MAX_HOURS" != "0" ]]; then
  MAX_SECONDS=$((MAX_HOURS * 3600))
else
  MAX_SECONDS=0
fi

check_time_limit() {
  if [[ "$MAX_SECONDS" -gt 0 ]]; then
    local elapsed=$(( $(date +%s) - START_TIME ))
    if [[ $elapsed -ge $MAX_SECONDS ]]; then
      echo -e "\n${YELLOW}Time limit reached (${MAX_HOURS}h). Stopping.${NC}"
      return 1
    fi
  fi
  return 0
}

# ---- Log directory ----------------------------------------------------------

mkdir -p "$LOG_DIR"

# ---- Dry run ----------------------------------------------------------------

if $DRY_RUN; then
  echo -e "${BOLD}${CYAN}Ralph Loop — Dry Run${NC}"
  echo ""
  echo "  Engine:          $ENGINE"
  echo "  Prompt file:     $PROMPT_FILE"
  echo "  Cooldown:        ${COOLDOWN}s"
  echo "  Max iterations:  ${MAX_ITERATIONS:-infinite}"
  echo "  Max hours:       ${MAX_HOURS:-infinite}"
  echo "  Log dir:         $LOG_DIR"
  if [[ "$ENGINE" == "claude" ]]; then
    echo "  Stream UI:       $STREAM_UI"
    [[ -n "$SYSTEM_PROMPT_FILE" ]] && echo "  System prompt:   $SYSTEM_PROMPT_FILE"
    echo "  Claude version:  $(claude --version 2>/dev/null || echo 'unknown')"
  fi
  if [[ "$ENGINE" == "codex" ]]; then
    [[ -n "$CODEX_MODEL" ]] && echo "  Model:           $CODEX_MODEL"
    [[ -n "$CODEX_PROFILE" ]] && echo "  Profile:         $CODEX_PROFILE"
    echo "  No MCP:          $NO_MCP"
    echo "  Dangerous:       $DANGEROUS"
    echo "  Codex version:   $(codex --version 2>/dev/null || echo 'unknown')"
  fi
  echo ""
  echo "  Prompt preview (first 5 lines):"
  head -5 "$PROMPT_FILE" | sed 's/^/    /'
  exit 0
fi

# ============================================================================
# Claude Code: Stream-JSON UI Renderer
# ============================================================================

render_event() {
  local line="$1"
  local type subtype

  type=$(echo "$line" | jq -r '.type // empty' 2>/dev/null) || return 0
  subtype=$(echo "$line" | jq -r '.subtype // empty' 2>/dev/null) || true

  case "$type" in
    system)
      if [[ "$subtype" == "init" ]]; then
        local model session
        model=$(echo "$line" | jq -r '.model // "unknown"')
        session=$(echo "$line" | jq -r '.session_id // "unknown"')
        echo -e "${DIM}  model: ${model}  session: ${session}${NC}"
      elif [[ "$subtype" == "compact_boundary" ]]; then
        echo -e "${DIM}  -- context compacted --${NC}"
      fi
      ;;
    stream_event)
      local delta_type delta_text
      delta_type=$(echo "$line" | jq -r '.event.delta.type // empty' 2>/dev/null) || true
      case "$delta_type" in
        text_delta)
          delta_text=$(echo "$line" | jq -rj '.event.delta.text // empty' 2>/dev/null) || true
          [[ -n "$delta_text" ]] && printf '%s' "$delta_text"
          ;;
        thinking_delta)
          delta_text=$(echo "$line" | jq -rj '.event.delta.thinking // empty' 2>/dev/null) || true
          [[ -n "$delta_text" ]] && printf "${DIM}%s${NC}" "$delta_text"
          ;;
      esac
      ;;
    assistant)
      local output
      output=$(echo "$line" | jq -r '
        .message.content[]? |
        if .type == "thinking" then
          "  \u001b[2m\u0001f4ad Thinking: \(.thinking | if length > 200 then .[:200] + "..." else . end)\u001b[0m"
        elif .type == "tool_use" then
          if .name == "Skill" then
            "  \u001b[1;35m\u26a1 Skill: \(.input.skill // "unknown")\u001b[0m" +
            if .input.args then " args: \(.input.args)" else "" end
          elif .name == "Agent" then
            "  \u001b[1;36m\u0001f916 Agent: \(.input.description // "unknown")\u001b[0m" +
            if .input.subagent_type then " [\(.input.subagent_type)]" else "" end
          elif .name == "Bash" then
            "  \u001b[1;33m\u25ba Bash\u001b[0m \(.input.command // "" | if length > 150 then .[:150] + "..." else . end)"
          elif .name == "Read" then
            "  \u001b[1;33m\u25ba Read\u001b[0m \(.input.file_path // "")"
          elif .name == "Write" then
            "  \u001b[1;33m\u25ba Write\u001b[0m \(.input.file_path // "")"
          elif .name == "Edit" then
            "  \u001b[1;33m\u25ba Edit\u001b[0m \(.input.file_path // "")"
          elif .name == "Glob" then
            "  \u001b[1;33m\u25ba Glob\u001b[0m \(.input.pattern // "")"
          elif .name == "Grep" then
            "  \u001b[1;33m\u25ba Grep\u001b[0m \(.input.pattern // "")"
          elif .name == "WebSearch" then
            "  \u001b[1;33m\u25ba WebSearch\u001b[0m \(.input.query // "")"
          elif .name == "WebFetch" then
            "  \u001b[1;33m\u25ba WebFetch\u001b[0m \(.input.url // "")"
          else
            "  \u001b[1;33m\u25ba \(.name)\u001b[0m \(.input | tostring | if length > 150 then .[:150] + "..." else . end)"
          end
        else empty
        end
      ' 2>/dev/null) || true
      [[ -n "$output" ]] && echo -e "$output"
      ;;
    user)
      local tool_results
      tool_results=$(echo "$line" | jq -r '
        .message.content[]? |
        select(.type == "tool_result") |
        if .is_error == true then
          "  \u001b[0;31m\u2717 \(.tool_use_id // "tool")\u001b[0m"
        else
          "  \u001b[0;32m\u2713 tool result\u001b[0m"
        end
      ' 2>/dev/null) || true
      [[ -n "$tool_results" ]] && echo -e "$tool_results"
      ;;
    tool_result)
      local tool_name is_error
      tool_name=$(echo "$line" | jq -r '.tool_name // "tool"' 2>/dev/null) || true
      is_error=$(echo "$line" | jq -r '.is_error // false' 2>/dev/null) || true
      if [[ "$is_error" == "true" ]]; then
        echo -e "  ${RED}x ${tool_name}${NC}"
      else
        echo -e "  ${GREEN}ok ${tool_name}${NC}"
      fi
      ;;
    rate_limit_event)
      local status resets_at
      status=$(echo "$line" | jq -r '.rate_limit_info.status // "unknown"' 2>/dev/null) || true
      if [[ "$status" != "allowed" ]]; then
        resets_at=$(echo "$line" | jq -r '.rate_limit_info.resetsAt // ""' 2>/dev/null) || true
        echo -e "  ${YELLOW}Rate limited (resets: ${resets_at})${NC}"
      fi
      ;;
    result)
      local cost duration_ms stop turns
      cost=$(echo "$line" | jq -r '.total_cost_usd // 0' 2>/dev/null) || true
      duration_ms=$(echo "$line" | jq -r '.duration_ms // 0' 2>/dev/null) || true
      stop=$(echo "$line" | jq -r '.stop_reason // "unknown"' 2>/dev/null) || true
      turns=$(echo "$line" | jq -r '.num_turns // 0' 2>/dev/null) || true
      echo ""
      echo -e "${DIM}  -- cost: \$${cost}  duration: ${duration_ms}ms  turns: ${turns}  stop: ${stop} --${NC}"
      ;;
  esac
}

# ============================================================================
# Codex: JSONL UI Renderer (--json output)
# ============================================================================

render_codex_event() {
  local line="$1"
  local type item_type

  type=$(echo "$line" | jq -r '.type // empty' 2>/dev/null) || return 0

  case "$type" in
    thread.started)
      local thread_id
      thread_id=$(echo "$line" | jq -r '.thread_id // "unknown"')
      echo -e "${DIM}  thread: ${thread_id}${NC}"
      ;;
    turn.started)
      echo -e "${DIM}  -- turn started --${NC}"
      ;;
    item.started)
      item_type=$(echo "$line" | jq -r '.item.type // empty' 2>/dev/null) || true
      if [[ "$item_type" == "command_execution" ]]; then
        local cmd
        cmd=$(echo "$line" | jq -r '.item.command // ""' 2>/dev/null) || true
        # Strip the shell wrapper prefix for display
        cmd="${cmd#/bin/zsh -lc }"
        cmd="${cmd#/bin/bash -lc }"
        echo -e "  ${YELLOW}> ${cmd}${NC}"
      fi
      ;;
    item.completed)
      item_type=$(echo "$line" | jq -r '.item.type // empty' 2>/dev/null) || true
      case "$item_type" in
        agent_message)
          local text
          text=$(echo "$line" | jq -r '.item.text // ""' 2>/dev/null) || true
          if [[ -n "$text" ]]; then
            echo -e "$text"
          fi
          ;;
        command_execution)
          local cmd exit_code output
          cmd=$(echo "$line" | jq -r '.item.command // ""' 2>/dev/null) || true
          cmd="${cmd#/bin/zsh -lc }"
          cmd="${cmd#/bin/bash -lc }"
          exit_code=$(echo "$line" | jq -r '.item.exit_code // ""' 2>/dev/null) || true
          output=$(echo "$line" | jq -r '.item.aggregated_output // ""' 2>/dev/null) || true
          if [[ "$exit_code" == "0" ]]; then
            echo -e "  ${GREEN}ok${NC} ${DIM}${cmd}${NC}"
          else
            echo -e "  ${RED}err (exit ${exit_code})${NC} ${DIM}${cmd}${NC}"
          fi
          if [[ -n "$output" ]]; then
            echo "$output" | head -5 | sed "s/^/  ${DIM}/" | sed "s/$/${NC}/"
          fi
          ;;
        file_edit|file_create)
          local path
          path=$(echo "$line" | jq -r '.item.path // .item.file // ""' 2>/dev/null) || true
          echo -e "  ${YELLOW}> ${item_type}${NC} ${path}"
          ;;
        *)
          # Catch-all for other item types
          local summary
          summary=$(echo "$line" | jq -r '.item | tostring | if length > 150 then .[:150] + "..." else . end' 2>/dev/null) || true
          [[ -n "$summary" && "$summary" != "{}" ]] && echo -e "  ${DIM}${item_type}: ${summary}${NC}"
          ;;
      esac
      ;;
    turn.completed)
      local input_tokens output_tokens cached
      input_tokens=$(echo "$line" | jq -r '.usage.input_tokens // 0' 2>/dev/null) || true
      output_tokens=$(echo "$line" | jq -r '.usage.output_tokens // 0' 2>/dev/null) || true
      cached=$(echo "$line" | jq -r '.usage.cached_input_tokens // 0' 2>/dev/null) || true
      echo ""
      echo -e "${DIM}  -- tokens: ${input_tokens} in (${cached} cached) / ${output_tokens} out --${NC}"
      ;;
  esac
}

# ============================================================================
# Engine Dispatch
# ============================================================================

run_claude() {
  local prompt="$1"
  local log_file="$2"

  local claude_args=(--print --dangerously-skip-permissions)

  if [[ -n "$SYSTEM_PROMPT_FILE" ]]; then
    claude_args+=(--system-prompt "$(cat "$SYSTEM_PROMPT_FILE")")
  fi

  if [[ "$STREAM_UI" == "true" ]]; then
    claude_args+=(--verbose --output-format stream-json --include-partial-messages)
    claude "${claude_args[@]}" -p "$prompt" 2>&1 \
      | tee "$log_file" \
      | while IFS= read -r line; do render_event "$line"; done
    return "${PIPESTATUS[0]}"
  else
    claude "${claude_args[@]}" -p "$prompt" 2>&1 | tee "$log_file"
    return "${PIPESTATUS[0]}"
  fi
}

run_codex() {
  local prompt="$1"
  local log_file="$2"

  local codex_args=(exec --color always)

  # Permission mode
  if [[ "$DANGEROUS" == "true" ]]; then
    codex_args+=(--dangerously-bypass-approvals-and-sandbox)
  else
    codex_args+=(--full-auto)
  fi

  # Disable remote MCP servers if requested
  if [[ "$NO_MCP" == "true" ]]; then
    codex_args+=(
      -c "mcp_servers.openaiDeveloperDocs.enabled=false"
      -c "mcp_servers.auggie.enabled=false"
      -c "mcp_servers.linear.enabled=false"
      -c "mcp_servers.context7.enabled=false"
    )
  fi

  # Model override
  if [[ -n "$CODEX_MODEL" ]]; then
    codex_args+=(-m "$CODEX_MODEL")
  fi

  # Profile
  if [[ -n "$CODEX_PROFILE" ]]; then
    codex_args+=(-p "$CODEX_PROFILE")
  fi

  # Output file for last message
  local output_file="${log_file%.log}.last.txt"
  codex_args+=(-o "$output_file")

  # Wrap prompt with Codex preamble
  local codex_prompt
  codex_prompt=$(cat <<EOF
Execute exactly one autonomous iteration for this repository.

Treat the attached instruction file as the canonical task spec. If it mentions
Claude Code, slash commands like /research, or an Agent tool, map those to the
closest Codex equivalent and continue.

Do one run, make the changes you judge appropriate, then exit cleanly.

${prompt}
EOF
)

  if [[ "$STREAM_UI" == "true" ]]; then
    codex_args+=(--json)
    echo "$codex_prompt" | codex "${codex_args[@]}" - 2>&1 \
      | tee "$log_file" \
      | while IFS= read -r line; do render_codex_event "$line"; done
    return "${PIPESTATUS[0]}"
  else
    echo "$codex_prompt" | codex "${codex_args[@]}" - 2>&1 | tee "$log_file"
    return "${PIPESTATUS[1]}"
  fi
}

# ============================================================================
# Main Loop
# ============================================================================

echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${BOLD}${GREEN}  Ralph Loop${NC}"
echo -e "${DIM}  Engine: ${ENGINE}  Prompt: ${PROMPT_FILE}${NC}"
if [[ "$ENGINE" == "claude" ]]; then
  echo -e "${DIM}  Claude version: $(claude --version 2>/dev/null || echo 'unknown')${NC}"
elif [[ "$ENGINE" == "codex" ]]; then
  echo -e "${DIM}  Codex version: $(codex --version 2>/dev/null || echo 'unknown')${NC}"
fi
echo -e "${DIM}  Cooldown: ${COOLDOWN}s  Max iterations: ${MAX_ITERATIONS:-0}  Max hours: ${MAX_HOURS:-0}${NC}"
echo -e "${DIM}  Logs: ${LOG_DIR}${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""

iteration=0

while true; do
  iteration=$((iteration + 1))

  # Check iteration limit
  if [[ "$MAX_ITERATIONS" -gt 0 && $iteration -gt $MAX_ITERATIONS ]]; then
    echo -e "\n${YELLOW}Max iterations reached ($MAX_ITERATIONS). Stopping.${NC}"
    break
  fi

  # Check time limit
  check_time_limit || break

  start_ts=$(date +%s)
  log_file="${LOG_DIR}/run-${iteration}.log"

  echo ""
  echo -e "${CYAN}----------------------------------------------------------------${NC}"
  echo -e "${BOLD}${GREEN}  Loop #${iteration}  $(date '+%Y-%m-%d %H:%M:%S')  [${ENGINE}]${NC}"
  echo -e "${DIM}  Prompt: ${PROMPT_FILE}  Log: ${log_file}${NC}"
  echo -e "${CYAN}----------------------------------------------------------------${NC}"
  echo ""

  PROMPT="$(cat "$PROMPT_FILE")"

  set +e
  if [[ "$ENGINE" == "claude" ]]; then
    run_claude "$PROMPT" "$log_file"
    exit_code=$?
  else
    run_codex "$PROMPT" "$log_file"
    exit_code=$?
  fi
  set -e

  end_ts=$(date +%s)
  duration=$((end_ts - start_ts))

  echo ""
  if [[ $exit_code -eq 0 ]]; then
    echo -e "${GREEN}  Done: loop #${iteration} in ${duration}s${NC}"
  else
    echo -e "${RED}  Failed: loop #${iteration} exit=${exit_code} after ${duration}s${NC}"
  fi

  echo -e "${DIM}  Cooldown ${COOLDOWN}s... (Ctrl+C to stop)${NC}"
  sleep "$COOLDOWN"
done

echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${BOLD}${GREEN}  Ralph Loop complete.${NC}"
echo -e "${DIM}  Iterations: $((iteration - 1))  Engine: ${ENGINE}  Logs: ${LOG_DIR}${NC}"
echo -e "${CYAN}================================================================${NC}"
