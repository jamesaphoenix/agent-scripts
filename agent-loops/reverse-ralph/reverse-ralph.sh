#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Reverse Ralph Loop — Spec Extraction from Existing Codebases
# ============================================================================
# Mines an existing codebase module by module, generating specs through
# configurable phases (default: overview -> PRD -> design doc).
#
# Supports both Claude Code (headless) and OpenAI Codex as engines.
# Runs inside tmux so you can attach/detach to watch.
#
# Configuration:
#   Copy reverse-ralph.config.example to reverse-ralph.config and fill in
#   your project-specific settings, or pass everything via flags/env vars.
#
# Usage:
#   ./reverse-ralph.sh                                # Auto-detect engine
#   ./reverse-ralph.sh --engine claude                 # Force Claude Code
#   ./reverse-ralph.sh --engine codex                  # Force Codex
#   ./reverse-ralph.sh --source-dir ./my-app           # Source codebase path
#   ./reverse-ralph.sh --source-repo <git-url>         # Clone if missing
#   ./reverse-ralph.sh --plan-file modules.md          # Custom plan file
#   ./reverse-ralph.sh --phases "overview,prd,design"  # Custom phases
#   ./reverse-ralph.sh --instruction-file instruct.md  # Custom instruction template
#   ./reverse-ralph.sh --resume                        # Skip completed modules
#   ./reverse-ralph.sh --module <slug>                 # Only process one module
#   ./reverse-ralph.sh --dry-run                       # Show what would be processed
#   ./reverse-ralph.sh --no-tmux                       # Run in current terminal
#   ./reverse-ralph.sh --help                          # Show help
#
#   tmux attach -t reverse-ralph                       # Watch it work
#   Ctrl+B, D                                          # Detach (loop keeps running)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Load config file if present -------------------------------------------

CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/reverse-ralph.config}"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

# ---- Defaults (overridden by config file, then by flags) -------------------

# Source codebase to reverse-engineer
SOURCE_DIR="${SOURCE_DIR:-}"
SOURCE_REPO_URL="${SOURCE_REPO_URL:-}"
SOURCE_CLONE_DEPTH="${SOURCE_CLONE_DEPTH:-1}"

# Plan file: markdown table with columns | # | Name | Slug | Status | Phase |
PLAN_FILE="${PLAN_FILE:-$SCRIPT_DIR/plan.md}"

# Prompt template with __PLACEHOLDERS__ for variable substitution
PROMPT_TEMPLATE="${PROMPT_TEMPLATE:-$SCRIPT_DIR/prompt.md}"

# Optional: instruction template file per phase (see build_instruction)
INSTRUCTION_FILE="${INSTRUCTION_FILE:-}"

# Phases to run (comma-separated, in order)
PHASES="${PHASES:-overview,prd,design-doc}"

# Output directories (relative to REPO_ROOT)
SPECS_DIR="${SPECS_DIR:-specs}"

# Engine
ENGINE="${ENGINE:-auto}"
CODEX_MODEL="${CODEX_MODEL:-}"
CODEX_PROFILE="${CODEX_PROFILE:-}"
DANGEROUS="${DANGEROUS:-true}"
NO_MCP="${NO_MCP:-false}"

# Internal
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PROGRESS_FILE="${PROGRESS_FILE:-$SCRIPT_DIR/progress.txt}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"

# Project context for instruction generation
PROJECT_NAME="${PROJECT_NAME:-the source codebase}"
OUR_STACK_DESCRIPTION="${OUR_STACK_DESCRIPTION:-}"

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

Mines an existing codebase module by module, generating specs through
configurable phases (default: overview -> PRD -> design doc).

SETUP:
  1. Copy reverse-ralph.config.example to reverse-ralph.config
  2. Fill in SOURCE_DIR, PLAN_FILE, and other project-specific settings
  3. Create a plan.md with a markdown table of modules to process
  4. Optionally create a prompt.md template with __PLACEHOLDERS__

USAGE:
  ./reverse-ralph.sh [OPTIONS]

OPTIONS:
  --engine <claude|codex|auto>   Agent engine (default: auto-detect)
  --source-dir <path>            Path to source codebase to reverse-engineer
  --source-repo <url>            Git URL to clone if source-dir doesn't exist
  --plan-file <path>             Markdown plan with module table (default: ./plan.md)
  --phases <p1,p2,...>           Comma-separated phases (default: overview,prd,design-doc)
  --instruction-file <path>      Custom instruction template per phase
  --resume                       Skip modules already marked done
  --module <slug>                Only process this module
  --dry-run                      Show what would be processed
  --no-tmux                      Run in current terminal (no tmux wrapper)

  Codex-specific:
  --model <model>                Model override (e.g., o4-mini)
  --codex-profile <name>         Codex profile name
  --dangerous                    Bypass sandbox entirely (default: true)
  --no-mcp                       Disable remote MCP servers

  --help                         Show this help

ENVIRONMENT VARIABLES:
  SOURCE_DIR, SOURCE_REPO_URL, PLAN_FILE, PROMPT_TEMPLATE, PHASES,
  SPECS_DIR, PROJECT_NAME, OUR_STACK_DESCRIPTION, ENGINE, CODEX_MODEL,
  CODEX_PROFILE, DANGEROUS, NO_MCP, REPO_ROOT, CONFIG_FILE

PLAN FILE FORMAT:
  A markdown table with these columns (parsed by pipe delimiter):

  | # | Name | Slug | Status | Phase |
  |---|------|------|--------|-------|
  | 1 | Authentication | auth | pending | overview |
  | 2 | Billing | billing | pending | overview |
  | 3 | Media Upload | media | done | design-doc |

  Status values: pending, in-progress, done, failed
  Phase values: whatever you set in --phases (default: overview, prd, design-doc)

PROMPT TEMPLATE:
  The prompt.md file supports these __PLACEHOLDERS__:
  __MODULE_NAME__    -> module display name (e.g. "Authentication")
  __MODULE_SLUG__    -> module slug (e.g. "auth")
  __MODULE_UPPER__   -> uppercased slug (e.g. "AUTH")
  __PHASE__          -> current phase (e.g. "overview")
  __SOURCE_PATHS__   -> extracted source paths from plan file

EXAMPLES:
  # Basic: reverse-engineer a local codebase
  ./reverse-ralph.sh --source-dir ./my-app --plan-file modules.md

  # Clone and process with Codex
  ./reverse-ralph.sh --engine codex --source-repo https://github.com/org/repo.git

  # Custom phases: just overview + PRD (no design doc)
  ./reverse-ralph.sh --phases "overview,prd"

  # Resume after interruption
  ./reverse-ralph.sh --resume
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
    --engine)           ENGINE="$2"; shift 2 ;;
    --source-dir)       SOURCE_DIR="$2"; shift 2 ;;
    --source-repo)      SOURCE_REPO_URL="$2"; shift 2 ;;
    --plan-file)        PLAN_FILE="$2"; shift 2 ;;
    --phases)           PHASES="$2"; shift 2 ;;
    --instruction-file) INSTRUCTION_FILE="$2"; shift 2 ;;
    --resume)           RESUME=true; shift ;;
    --module)           SINGLE_MODULE="$2"; shift 2 ;;
    --dry-run)          DRY_RUN=true; shift ;;
    --no-tmux)          NO_TMUX=true; shift ;;
    --model)            CODEX_MODEL="$2"; shift 2 ;;
    --codex-profile)    CODEX_PROFILE="$2"; shift 2 ;;
    --dangerous)        DANGEROUS=true; shift ;;
    --no-mcp)           NO_MCP=true; shift ;;
    --help|-h)          show_help ;;
    *) echo "Unknown flag: $1 (try --help)"; exit 1 ;;
  esac
done

# ---- Build phase list from comma-separated string --------------------------

IFS=',' read -ra PHASE_LIST <<< "$PHASES"

next_phase() {
  local current="$1"
  local found=false
  for p in "${PHASE_LIST[@]}"; do
    if $found; then
      echo "$p"
      return
    fi
    if [[ "$p" == "$current" ]]; then
      found=true
    fi
  done
  echo "done"
}

first_phase() {
  echo "${PHASE_LIST[0]}"
}

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

for cmd in git; do
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
  [[ -n "$SOURCE_DIR" ]] && args+=("--source-dir" "$SOURCE_DIR")
  [[ -n "$SOURCE_REPO_URL" ]] && args+=("--source-repo" "$SOURCE_REPO_URL")
  [[ "$PLAN_FILE" != "$SCRIPT_DIR/plan.md" ]] && args+=("--plan-file" "$PLAN_FILE")
  [[ "$PHASES" != "overview,prd,design-doc" ]] && args+=("--phases" "$PHASES")
  [[ -n "$INSTRUCTION_FILE" ]] && args+=("--instruction-file" "$INSTRUCTION_FILE")
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

# ---- Ensure source codebase exists -----------------------------------------

ensure_source() {
  if [[ -z "$SOURCE_DIR" ]]; then
    log "No --source-dir specified, skipping source clone"
    return
  fi

  if [[ -d "$SOURCE_DIR" ]]; then
    log "Source codebase found at $SOURCE_DIR"
  elif [[ -n "$SOURCE_REPO_URL" ]]; then
    log "Cloning source from $SOURCE_REPO_URL..."
    git clone --depth "$SOURCE_CLONE_DEPTH" "$SOURCE_REPO_URL" "$SOURCE_DIR"
    ok "Cloned to $SOURCE_DIR"
  else
    err "Source directory not found: $SOURCE_DIR"
    err "Provide --source-repo <url> to clone it automatically"
    exit 1
  fi
}

# ---- Extract modules from plan file ----------------------------------------

extract_modules() {
  if [[ ! -f "$PLAN_FILE" ]]; then
    err "Plan file not found: $PLAN_FILE"
    err "Create one with a markdown table: | # | Name | Slug | Status | Phase |"
    exit 1
  fi
  grep -E '^\| [0-9]+ \|' "$PLAN_FILE" | while IFS='|' read -r _ num name slug status phase _; do
    slug=$(echo "$slug" | xargs)
    name=$(echo "$name" | xargs)
    status=$(echo "$status" | xargs)
    phase=$(echo "$phase" | xargs)
    echo "${slug}|${name}|${status}|${phase}"
  done
}

# ---- Extract source paths for a module from plan file ----------------------
# Looks for a markdown section matching the module slug and collects
# bullet-pointed paths (- `path/to/file`).

extract_source_paths() {
  local slug="$1"
  local in_section=false
  local paths=""

  while IFS= read -r line; do
    # Match section headers like "### 1. module-name" or "### module-name" or "## module-name"
    if [[ "$line" =~ ^#{2,3}[[:space:]].*${slug} ]]; then
      in_section=true
      continue
    elif [[ "$line" =~ ^#{2,3}[[:space:]] ]] && $in_section; then
      break
    fi

    if $in_section && [[ "$line" =~ ^-[[:space:]]\` ]]; then
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

# ---- Build prompt from template --------------------------------------------

build_prompt() {
  local slug="$1" name="$2" phase="$3"
  local upper
  upper=$(echo "$slug" | tr '[:lower:]-' '[:upper:]_')

  if [[ ! -f "$PROMPT_TEMPLATE" ]]; then
    # No template — return empty (instruction will carry the context)
    echo ""
    return
  fi

  local source_paths
  source_paths=$(extract_source_paths "$slug")

  local prompt
  prompt=$(cat "$PROMPT_TEMPLATE")

  prompt="${prompt//__MODULE_NAME__/$name}"
  prompt="${prompt//__MODULE_SLUG__/$slug}"
  prompt="${prompt//__MODULE_UPPER__/$upper}"
  prompt="${prompt//__PHASE__/$phase}"
  prompt="${prompt//__SOURCE_PATHS__/$source_paths}"
  prompt="${prompt//__PROJECT_NAME__/$PROJECT_NAME}"
  prompt="${prompt//__SOURCE_DIR__/${SOURCE_DIR:-./source}}"

  echo "$prompt"
}

# ---- Build instruction per phase -------------------------------------------
# If --instruction-file is set, reads that and substitutes placeholders.
# Otherwise generates a sensible default per phase.

build_instruction() {
  local slug="$1" name="$2" phase="$3"
  local upper
  upper=$(echo "$slug" | tr '[:lower:]-' '[:upper:]_')
  local source_dir_display="${SOURCE_DIR:-.}"

  # If custom instruction file provided, use it with placeholder substitution
  if [[ -n "$INSTRUCTION_FILE" && -f "$INSTRUCTION_FILE" ]]; then
    local instruction
    instruction=$(cat "$INSTRUCTION_FILE")
    instruction="${instruction//__MODULE_NAME__/$name}"
    instruction="${instruction//__MODULE_SLUG__/$slug}"
    instruction="${instruction//__MODULE_UPPER__/$upper}"
    instruction="${instruction//__PHASE__/$phase}"
    instruction="${instruction//__SOURCE_DIR__/$source_dir_display}"
    instruction="${instruction//__PROJECT_NAME__/$PROJECT_NAME}"
    instruction="${instruction//__SPECS_DIR__/$SPECS_DIR}"
    instruction="${instruction//__OUR_STACK__/$OUR_STACK_DESCRIPTION}"
    echo "$instruction"
    return
  fi

  # Default instructions per phase
  case "$phase" in
    overview)
      cat <<EOF
You are extracting a spec from an existing codebase at ${source_dir_display}/.

Read the source files for the '${name}' module. The source paths are listed in the system prompt.

Generate an overview spec and save it to ${SPECS_DIR}/${slug}/overview.md.

Before starting, thoroughly read every source file listed in the system prompt so you have full context. Flag UNCLEAR and INVESTIGATE items. Be specific with endpoint paths, model names, and parameter types.

The overview should describe what the code ACTUALLY DOES, not what we want to build. This is reverse engineering.
EOF
      ;;
    prd)
      cat <<EOF
You are continuing the reverse Ralph extraction for '${name}'.

First, read the overview spec at ${SPECS_DIR}/${slug}/overview.md to understand what the module does.

Then generate a PRD and save it to ${SPECS_DIR}/prd/${slug}.md.

Use EARS syntax with IDs REQ-${upper}-NNN. Map every significant behavior from the overview into a traceable EARS requirement. If you need to fill gaps, read source files from ${source_dir_display}/.

The PRD should describe REQUIREMENTS for what we want to build (based on what the source does), not implementation details.
EOF
      ;;
    design-doc)
      local stack_section=""
      if [[ -n "$OUR_STACK_DESCRIPTION" ]]; then
        stack_section="
IMPORTANT: The design doc describes what WE would build in OUR stack:
${OUR_STACK_DESCRIPTION}
"
      fi
      cat <<EOF
You are continuing the reverse Ralph extraction for '${name}'.

First, read:
1. ${SPECS_DIR}/${slug}/overview.md (the overview - what the source does)
2. ${SPECS_DIR}/prd/${slug}.md (the PRD - what we want to build)

Then generate a design doc and save it to ${SPECS_DIR}/design/${slug}.md.
${stack_section}
Map every must-priority EARS requirement to an invariant INV-${upper}-NNN.
EOF
      ;;
    *)
      # Generic fallback for custom phases
      cat <<EOF
You are working on phase '${phase}' of the reverse Ralph extraction for '${name}'.

Read any previous phase outputs in ${SPECS_DIR}/${slug}/ and ${SPECS_DIR}/*/${slug}.md.
Then generate the ${phase} document and save it to ${SPECS_DIR}/${phase}/${slug}.md.

Use the source code at ${source_dir_display}/ as your primary reference.
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

  local claude_args=(--print --verbose --dangerously-skip-permissions)

  if [[ -n "$system_prompt" ]]; then
    claude_args+=(--system-prompt "$system_prompt")
  fi

  claude "${claude_args[@]}" "$instruction" 2>&1 | tee "$log_file"
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

  # Ensure output directories exist for all configured phases
  mkdir -p "$REPO_ROOT/${SPECS_DIR}/${slug}" "$LOG_DIR"
  for p in "${PHASE_LIST[@]}"; do
    mkdir -p "$REPO_ROOT/${SPECS_DIR}/${p}"
  done

  update_plan_status "$slug" "in-progress" "$phase"
  progress "$slug" "$phase" "STARTED"
  start_ts=$(date +%s)

  local iter_prompt
  iter_prompt=$(build_prompt "$slug" "$name" "$phase")

  local instruction
  instruction=$(build_instruction "$slug" "$name" "$phase")

  log "Launching ${ENGINE} for: ${name} [${phase}]..."
  echo ""

  local exit_code=0
  dispatch_agent "$iter_prompt" "$instruction" "$LOG_DIR/${slug}-${phase}.log" || exit_code=$?

  local end_ts
  end_ts=$(date +%s)
  duration="$((end_ts - start_ts))s"

  if [[ $exit_code -eq 0 ]]; then
    ok "Completed: ${name} [${phase}] in ${duration}"
    progress "$slug" "$phase" "DONE" "$duration"

    local next
    next=$(next_phase "$phase")
    if [[ "$next" == "done" ]]; then
      local last_phase="${PHASE_LIST[${#PHASE_LIST[@]}-1]}"
      update_plan_status "$slug" "done" "$last_phase"
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
  |   Mining specs from source -> configurable phases     |
  +======================================================+
BANNER
  echo -e "${NC}"
  echo -e "${DIM}  Engine:  ${ENGINE}${NC}"
  echo -e "${DIM}  Phases:  ${PHASES}${NC}"
  [[ -n "$SOURCE_DIR" ]] && echo -e "${DIM}  Source:  ${SOURCE_DIR}${NC}"
  echo -e "${DIM}  Plan:    ${PLAN_FILE}${NC}"
  echo -e "${DIM}  Specs:   ${SPECS_DIR}${NC}"
  if [[ "$ENGINE" == "claude" ]]; then
    echo -e "${DIM}  Claude:  $(claude --version 2>/dev/null || echo 'unknown')${NC}"
  else
    echo -e "${DIM}  Codex:   $(codex --version 2>/dev/null || echo 'unknown')${NC}"
  fi
  echo ""

  ensure_source

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
    $DRY_RUN && break

    log "Iteration #${iteration} complete. Next pass..."
    echo ""
  done

  hr
  echo -e "${BOLD}${GREEN}"
  echo "  Reverse Ralph complete."
  echo "  Engine:   $ENGINE"
  echo "  Specs:    $REPO_ROOT/${SPECS_DIR}/"
  echo "  Logs:     $LOG_DIR/"
  echo "  Progress: $PROGRESS_FILE"
  echo -e "${NC}"
  hr
}

main
