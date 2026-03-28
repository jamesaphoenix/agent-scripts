#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Reverse Ralph Loop — Spec Extraction from Existing Codebases
# ============================================================================
# Mines an existing codebase module by module, generating:
#   1. Overview spec  -> specs/<slug>/overview.md
#   2. PRD            -> specs/prd/<slug>.md
#   3. Design doc     -> specs/design/<slug>.md
#
# Supports both Claude Code (headless) and OpenAI Codex as engines.
# Runs inside tmux so you can attach/detach to watch.
#
# Usage:
#   ./reverse-ralph.sh                          # Auto-detect engine
#   ./reverse-ralph.sh --engine claude           # Force Claude Code
#   ./reverse-ralph.sh --engine codex            # Force Codex
#   ./reverse-ralph.sh --resume                  # Skip completed modules
#   ./reverse-ralph.sh --module <slug>           # Only process one module
#   ./reverse-ralph.sh --dry-run                 # Show what would be processed
#   ./reverse-ralph.sh --no-tmux                 # Run in current terminal
#   ./reverse-ralph.sh --help                    # Show help
#
#   tmux attach -t reverse-ralph                 # Watch it work
#   Ctrl+B, D                                    # Detach (loop keeps running)
#
# Codex-specific:
#   ./reverse-ralph.sh --engine codex --model o4-mini
#   ./reverse-ralph.sh --engine codex --dangerous
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
POSTIZ_DIR="$REPO_ROOT/postiz-app"
PLAN_FILE="$SCRIPT_DIR/implementation-plan.md"
PROMPT_TEMPLATE="$SCRIPT_DIR/prompt.md"
PROGRESS_FILE="$SCRIPT_DIR/progress.txt"
LOG_DIR="$SCRIPT_DIR/logs"

# ---- Defaults ---------------------------------------------------------------

ENGINE="${ENGINE:-auto}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_PROFILE="${CODEX_PROFILE:-}"
DANGEROUS="${DANGEROUS:-true}"
NO_MCP="${NO_MCP:-false}"

# ---- Colours ----------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ---- Help -------------------------------------------------------------------

show_help() {
  cat <<'HELP'
Reverse Ralph Loop — Spec Extraction (Claude Code + Codex)

Mines an existing codebase module by module, generating overview specs,
PRDs, and design docs.

USAGE:
  ./reverse-ralph.sh [OPTIONS]

OPTIONS:
  --engine <claude|codex|auto>   Agent engine (default: auto-detect)
  --resume                       Skip modules already marked done
  --module <slug>                Only process this module
  --dry-run                      Show what would be processed
  --no-tmux                      Run in current terminal (no tmux wrapper)

  Codex-specific:
  --model <model>                Model override (e.g., o4-mini)
  --codex-profile <name>         Codex profile name
  --dangerous                    Bypass sandbox entirely
  --no-mcp                       Disable remote MCP servers

  --help                         Show this help

ENVIRONMENT VARIABLES:
  ENGINE, CODEX_MODEL, CODEX_PROFILE, DANGEROUS, NO_MCP

EXAMPLES:
  # Auto-detect engine, process all modules
  ./reverse-ralph.sh

  # Codex, single module
  ./reverse-ralph.sh --engine codex --module auth

  # Resume interrupted run with Claude
  ./reverse-ralph.sh --engine claude --resume
HELP
  exit 0
}

# ---- Parse flags ------------------------------------------------------------

RESUME=false
SINGLE_MODULE=""
DRY_RUN=false
NO_TMUX=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --engine)        ENGINE="$2"; shift 2 ;;
    --resume)        RESUME=true; shift ;;
    --module)        SINGLE_MODULE="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=true; shift ;;
    --no-tmux)       NO_TMUX=true; shift ;;
    --model)         CODEX_MODEL="$2"; shift 2 ;;
    --codex-profile) CODEX_PROFILE="$2"; shift 2 ;;
    --dangerous)     DANGEROUS=true; shift ;;
    --no-mcp)        NO_MCP=true; shift ;;
    --help|-h)       show_help ;;
    *) echo "Unknown flag: $1 (try --help)"; exit 1 ;;
  esac
done

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
  exit 1
fi

if [[ "$ENGINE" != "claude" && "$ENGINE" != "codex" ]]; then
  echo -e "${RED}Error: Unknown engine '${ENGINE}'. Use 'claude', 'codex', or 'auto'.${NC}" >&2
  exit 1
fi

# ---- Dependency check -------------------------------------------------------

for cmd in git jq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo -e "${RED}Error: '$cmd' is required but not found in PATH${NC}" >&2
    exit 1
  fi
done

if [[ "$ENGINE" == "claude" ]] && ! command -v claude &>/dev/null; then
  echo -e "${RED}Error: 'claude' not found in PATH${NC}" >&2
  exit 1
fi

if [[ "$ENGINE" == "codex" ]] && ! command -v codex &>/dev/null; then
  echo -e "${RED}Error: 'codex' not found in PATH${NC}" >&2
  exit 1
fi

# ---- Tmux wrapper -----------------------------------------------------------

TMUX_SESSION="reverse-ralph"

if ! $NO_TMUX && ! $DRY_RUN && command -v tmux &>/dev/null && [[ -z "${TMUX:-}" ]]; then
  args=("--no-tmux" "--engine" "$ENGINE")
  $RESUME && args+=("--resume")
  [[ -n "$SINGLE_MODULE" ]] && args+=("--module" "$SINGLE_MODULE")
  [[ -n "$CODEX_MODEL" ]] && args+=("--model" "$CODEX_MODEL")
  [[ -n "$CODEX_PROFILE" ]] && args+=("--codex-profile" "$CODEX_PROFILE")
  [[ "$DANGEROUS" == "true" ]] && args+=("--dangerous")
  [[ "$NO_MCP" == "true" ]] && args+=("--no-mcp")

  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

  echo -e "${CYAN}Starting tmux session: ${BOLD}${TMUX_SESSION}${NC}"
  echo -e "${CYAN}Engine: ${BOLD}${ENGINE}${NC}"
  echo -e "${CYAN}Attach with:  tmux attach -t ${TMUX_SESSION}${NC}"
  echo -e "${CYAN}Detach with:  Ctrl+B, D${NC}"
  echo ""

  tmux new-session -d -s "$TMUX_SESSION" -c "$REPO_ROOT" \
    "$SCRIPT_DIR/reverse-ralph.sh ${args[*]}"

  exec tmux attach -t "$TMUX_SESSION"
fi

# ---- Helpers ----------------------------------------------------------------

log() { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*"; }
ok()  { echo -e "${GREEN}[$(date '+%H:%M:%S')] ok${NC} $*"; }
warn(){ echo -e "${YELLOW}[$(date '+%H:%M:%S')] warn${NC} $*"; }
err() { echo -e "${RED}[$(date '+%H:%M:%S')] err${NC} $*"; }
hr()  { echo -e "${CYAN}================================================================${NC}"; }

progress() {
  local slug="$1" phase="$2" status="$3" duration="${4:-}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $slug $phase $status $duration" >> "$PROGRESS_FILE"
}

update_plan_status() {
  local slug="$1" new_status="$2" new_phase="$3"
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s/| ${slug} | [a-z-]* | [a-z-]* |/| ${slug} | ${new_status} | ${new_phase} |/" "$PLAN_FILE"
  else
    sed -i "s/| ${slug} | [a-z-]* | [a-z-]* |/| ${slug} | ${new_status} | ${new_phase} |/" "$PLAN_FILE"
  fi
}

next_phase() {
  case "$1" in
    overview)    echo "prd" ;;
    prd)         echo "design-doc" ;;
    design-doc)  echo "done" ;;
    *)           echo "overview" ;;
  esac
}

# ---- Clone source repo if needed -------------------------------------------

clone_postiz() {
  if [[ -d "$POSTIZ_DIR" ]]; then
    log "Source repo found at $POSTIZ_DIR"
  else
    log "Cloning postiz-app..."
    git clone --depth 1 https://github.com/gitroomhq/postiz-app.git "$POSTIZ_DIR"
    ok "Cloned postiz-app"
  fi
}

# ---- Extract modules from plan ----------------------------------------------

extract_modules() {
  grep -E '^\| [0-9]+ \|' "$PLAN_FILE" | while IFS='|' read -r _ num name slug status phase _; do
    slug=$(echo "$slug" | xargs)
    name=$(echo "$name" | xargs)
    status=$(echo "$status" | xargs)
    phase=$(echo "$phase" | xargs)
    echo "${slug}|${name}|${status}|${phase}"
  done
}

# ---- Extract source paths for a module --------------------------------------

extract_source_paths() {
  local slug="$1"
  local in_section=false
  local paths=""

  local section_num=""
  case "$slug" in
    post-management)        section_num="1" ;;
    social-integrations)    section_num="2" ;;
    auth)                   section_num="3" ;;
    org-management)         section_num="4" ;;
    analytics)              section_num="5" ;;
    public-api)             section_num="6" ;;
    billing)                section_num="7" ;;
    media)                  section_num="8" ;;
    webhooks)               section_num="9" ;;
    autopost)               section_num="10" ;;
    oauth-apps)             section_num="11" ;;
    notifications)          section_num="12" ;;
    signatures)             section_num="13" ;;
    sets)                   section_num="14" ;;
    short-linking)          section_num="15" ;;
    temporal-orchestration) section_num="16" ;;
    third-party)            section_num="17" ;;
  esac

  while IFS= read -r line; do
    if [[ "$line" =~ ^###\ ${section_num}\. ]]; then
      in_section=true
      continue
    elif [[ "$line" =~ ^### ]] && $in_section; then
      break
    fi

    if $in_section && [[ "$line" =~ ^-\ \` ]]; then
      local path
      path="${line#- \`}"
      path="${path%\`*}"
      if [[ -n "$path" ]]; then
        paths="${paths}- \`${path}\`"$'\n'
      fi
    fi
  done < "$PLAN_FILE"

  printf '%s' "$paths"
}

# ---- Build prompt + instruction ---------------------------------------------

build_prompt() {
  local slug="$1" name="$2" phase="$3"
  local upper
  upper=$(echo "$slug" | tr '[:lower:]-' '[:upper:]_')
  local source_paths
  source_paths=$(extract_source_paths "$slug")

  local prompt
  prompt=$(cat "$PROMPT_TEMPLATE")

  prompt="${prompt//__MODULE_NAME__/$name}"
  prompt="${prompt//__MODULE_SLUG__/$slug}"
  prompt="${prompt//__MODULE_UPPER__/$upper}"
  prompt="${prompt//__PHASE__/$phase}"
  prompt="${prompt//__SOURCE_PATHS__/$source_paths}"

  echo "$prompt"
}

build_instruction() {
  local slug="$1" name="$2" phase="$3"
  local upper
  upper=$(echo "$slug" | tr '[:lower:]-' '[:upper:]_')

  case "$phase" in
    overview)
      cat <<EOF
You are extracting a spec from an existing open-source codebase (Postiz) at ./postiz-app/.

Read the source files for the '${name}' module. The source paths are listed in the system prompt.

Generate an overview spec and save it to specs/${slug}/overview.md.

Before starting, thoroughly read every source file listed in the system prompt so you have full context. Flag UNCLEAR and INVESTIGATE items. Be specific with endpoint paths, Prisma model names, and parameter types.

The overview should describe what the Postiz code ACTUALLY DOES, not what we want to build. This is reverse engineering.
EOF
      ;;
    prd)
      cat <<EOF
You are continuing the reverse Ralph extraction for '${name}'.

First, read the overview spec at specs/${slug}/overview.md to understand what the module does.

Then generate a PRD and save it to specs/prd/${slug}.md.

Use EARS syntax with IDs REQ-${upper}-NNN. Map every significant behavior from the overview into a traceable EARS requirement. If you need to fill gaps, read source files from ./postiz-app/.

The PRD should describe REQUIREMENTS for what we want to build (based on what Postiz does), not implementation details.
EOF
      ;;
    design-doc)
      cat <<EOF
You are continuing the reverse Ralph extraction for '${name}'.

First, read:
1. specs/${slug}/overview.md (the overview - what Postiz does)
2. specs/prd/${slug}.md (the PRD - what we want to build)

Then generate a design doc and save it to specs/design/${slug}.md.

IMPORTANT: The design doc describes what WE would build in OUR stack:
- NestJS controllers -> Effect HttpApi endpoints
- Prisma models -> Drizzle schema
- NestJS services -> Effect services (DDD layers)
- Their Temporal -> Our Temporal workflows
- CASL permissions -> Our permission system

Map every must-priority EARS requirement to an invariant INV-${upper}-NNN.
EOF
      ;;
  esac
}

# ---- Engine dispatch --------------------------------------------------------

dispatch_agent() {
  local system_prompt="$1"
  local instruction="$2"
  local log_file="$3"

  if [[ "$ENGINE" == "claude" ]]; then
    dispatch_claude "$system_prompt" "$instruction" "$log_file"
  else
    dispatch_codex "$system_prompt" "$instruction" "$log_file"
  fi
}

dispatch_claude() {
  local system_prompt="$1"
  local instruction="$2"
  local log_file="$3"

  claude --print \
    --verbose \
    --dangerously-skip-permissions \
    --system-prompt "$system_prompt" \
    "$instruction" \
    2>&1 | tee "$log_file"
}

dispatch_codex() {
  local system_prompt="$1"
  local instruction="$2"
  local log_file="$3"

  local codex_args=(exec --color always)

  if [[ "$DANGEROUS" == "true" ]]; then
    codex_args+=(--dangerously-bypass-approvals-and-sandbox)
  else
    codex_args+=(--full-auto)
  fi

  if [[ "$NO_MCP" == "true" ]]; then
    codex_args+=(
      -c "mcp_servers.openaiDeveloperDocs.enabled=false"
      -c "mcp_servers.auggie.enabled=false"
      -c "mcp_servers.linear.enabled=false"
      -c "mcp_servers.context7.enabled=false"
    )
  fi

  [[ -n "$CODEX_MODEL" ]] && codex_args+=(-m "$CODEX_MODEL")
  [[ -n "$CODEX_PROFILE" ]] && codex_args+=(-p "$CODEX_PROFILE")

  local output_file="${log_file%.log}.last.txt"
  codex_args+=(-o "$output_file")

  local full_prompt
  full_prompt=$(cat <<EOF
Context (system prompt):
${system_prompt}

---

Task:
${instruction}
EOF
)

  echo "$full_prompt" | codex "${codex_args[@]}" - 2>&1 | tee "$log_file"
  return "${PIPESTATUS[1]}"
}

# ---- Run one extraction phase -----------------------------------------------

run_phase() {
  local slug="$1" name="$2" phase="$3"
  local start_ts duration

  hr
  echo -e "${BOLD}${CYAN}"
  echo "  MODULE:  $name"
  echo "  SLUG:    $slug"
  echo "  PHASE:   $phase"
  echo "  ENGINE:  $ENGINE"
  echo -e "${NC}"
  hr

  if $DRY_RUN; then
    log "[DRY RUN] Would process $slug / $phase via $ENGINE"
    return 0
  fi

  mkdir -p "$REPO_ROOT/specs/${slug}" "$REPO_ROOT/specs/prd" "$REPO_ROOT/specs/design" "$LOG_DIR"

  update_plan_status "$slug" "in-progress" "$phase"
  progress "$slug" "$phase" "STARTED"
  start_ts=$(date +%s)

  local iter_prompt="$SCRIPT_DIR/.current-prompt.md"
  build_prompt "$slug" "$name" "$phase" > "$iter_prompt"

  local instruction
  instruction=$(build_instruction "$slug" "$name" "$phase")

  log "Launching ${ENGINE} for: ${name} [${phase}]..."
  echo ""

  local exit_code=0
  dispatch_agent "$(cat "$iter_prompt")" "$instruction" "$LOG_DIR/${slug}-${phase}.log" || exit_code=$?

  local end_ts
  end_ts=$(date +%s)
  duration="$((end_ts - start_ts))s"

  if [[ $exit_code -eq 0 ]]; then
    ok "Completed: ${name} [${phase}] in ${duration}"
    progress "$slug" "$phase" "DONE" "$duration"

    local next
    next=$(next_phase "$phase")
    if [[ "$next" == "done" ]]; then
      update_plan_status "$slug" "done" "design-doc"
    else
      update_plan_status "$slug" "pending" "$next"
    fi
  else
    err "Failed: ${name} [${phase}] (exit code: ${exit_code})"
    progress "$slug" "$phase" "FAILED" "$duration"
    update_plan_status "$slug" "failed" "$phase"
  fi

  echo ""
  log "Phase complete. Press ENTER to continue, or Ctrl+C to stop."
  read -r
}

# ---- Main loop --------------------------------------------------------------

main() {
  echo -e "${BOLD}${CYAN}"
  cat << 'BANNER'
  +======================================================+
  |          REVERSE RALPH LOOP -- Spec Extractor         |
  |                                                       |
  |   Mining specs from source -> PRD + Design Docs       |
  +======================================================+
BANNER
  echo -e "${NC}"
  echo -e "${DIM}  Engine: ${ENGINE}${NC}"
  if [[ "$ENGINE" == "claude" ]]; then
    echo -e "${DIM}  Claude version: $(claude --version 2>/dev/null || echo 'unknown')${NC}"
  else
    echo -e "${DIM}  Codex version: $(codex --version 2>/dev/null || echo 'unknown')${NC}"
  fi
  echo ""

  clone_postiz

  local iteration=0

  while true; do
    iteration=$((iteration + 1))
    local found_work=false

    hr
    log "Loop iteration #${iteration} -- scanning for work..."
    echo ""

    while IFS='|' read -r slug name status phase; do
      [[ -z "$slug" ]] && continue

      if [[ -n "$SINGLE_MODULE" && "$slug" != "$SINGLE_MODULE" ]]; then
        continue
      fi

      if [[ "$status" == "done" ]]; then
        continue
      fi

      if [[ "$status" == "failed" && $RESUME == true ]]; then
        warn "Skipping failed: $name ($slug) -- reset in plan to retry"
        continue
      fi

      found_work=true

      run_phase "$slug" "$name" "$phase"

      local current_phase="$phase"
      while true; do
        local next
        next=$(next_phase "$current_phase")
        [[ "$next" == "done" ]] && break
        run_phase "$slug" "$name" "$next"
        current_phase="$next"
      done

    done < <(extract_modules)

    if ! $found_work; then
      echo ""
      ok "All modules processed!"
      break
    fi

    [[ -n "$SINGLE_MODULE" ]] && break

    log "Iteration #${iteration} complete. Next pass..."
    echo ""
  done

  hr
  echo -e "${BOLD}${GREEN}"
  echo "  Reverse Ralph complete."
  echo "  Engine:   $ENGINE"
  echo "  Specs:    $REPO_ROOT/specs/"
  echo "  Logs:     $LOG_DIR/"
  echo "  Progress: $PROGRESS_FILE"
  echo -e "${NC}"
  hr
}

main
