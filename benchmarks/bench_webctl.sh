#!/usr/bin/env bash
# Benchmark: run a task with Claude + webctl
# Usage: ./bench_webctl.sh <task_number> [output_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/tasks.sh"
source "$SCRIPT_DIR/bench_eval.sh"

TASK_NUM="${1:?Usage: $0 <task_number 1-4> [output_dir]}"
OUT_DIR="${2:-$SCRIPT_DIR/results}"
mkdir -p "$OUT_DIR"

TASK_NAME_VAR="TASK${TASK_NUM}_NAME"
TASK_PROMPT_VAR="TASK${TASK_NUM}_PROMPT"
TASK_NAME="${!TASK_NAME_VAR}"
TASK_PROMPT="${!TASK_PROMPT_VAR}"

if [[ -z "$TASK_NAME" ]]; then
  echo "ERROR: Unknown task $TASK_NUM" >&2
  exit 1
fi

echo "=== webctl benchmark: $TASK_NAME ==="
echo "Task: $TASK_PROMPT"
echo ""

# Clean slate: stop any existing session/daemon
uv run --project "$PROJECT_DIR" webctl stop 2>/dev/null || true
sleep 0.5

WEBCTL="uv run --project $PROJECT_DIR webctl"

# Enable command logging if not already set
export WEBCTL_LOG="${WEBCTL_LOG:-$OUT_DIR/webctl_${TASK_NAME}.log}"
> "$WEBCTL_LOG"  # truncate

RESULT=$(claude -p \
  --output-format json \
  --plugin-dir "$PROJECT_DIR" \
  --allowedTools "Bash(uv run *webctl*)" \
  --permission-mode bypassPermissions \
  --max-budget-usd 1.00 \
  --append-system-prompt "IMPORTANT: Run webctl via: $WEBCTL <command>. Be efficient. When done, output your final answer clearly." \
  "$TASK_PROMPT" < /dev/null 2>/dev/null)

bench_eval "$RESULT" "$TASK_NAME" "webctl" "$TASK_PROMPT" "$OUT_DIR"
