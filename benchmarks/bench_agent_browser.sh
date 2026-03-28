#!/usr/bin/env bash
# Benchmark: run a task with Claude + agent-browser
# Usage: ./bench_agent_browser.sh <task_number> [output_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/tasks.sh"
source "$SCRIPT_DIR/bench_eval.sh"

TASK_NUM="${1:?Usage: $0 <task_number 1-4> [output_dir]}"
OUT_DIR="${2:-$SCRIPT_DIR/results}"
mkdir -p "$OUT_DIR"

AB="$SCRIPT_DIR/agent-browser/node_modules/.bin/agent-browser"
if [[ ! -x "$AB" ]]; then
  echo "ERROR: agent-browser not found. Run: cd $SCRIPT_DIR/agent-browser && npm install" >&2
  exit 1
fi

TASK_NAME_VAR="TASK${TASK_NUM}_NAME"
TASK_PROMPT_VAR="TASK${TASK_NUM}_PROMPT"
TASK_NAME="${!TASK_NAME_VAR}"
TASK_PROMPT="${!TASK_PROMPT_VAR}"

if [[ -z "$TASK_NAME" ]]; then
  echo "ERROR: Unknown task $TASK_NUM" >&2
  exit 1
fi

echo "=== agent-browser benchmark: $TASK_NAME ==="
echo "Task: $TASK_PROMPT"
echo ""

AB_PLUGIN_DIR="$SCRIPT_DIR/plugins/agent-browser"

RESULT=$(claude -p \
  --output-format json \
  --plugin-dir "$AB_PLUGIN_DIR" \
  --allowedTools "Bash($AB *)" \
  --permission-mode bypassPermissions \
  --max-budget-usd 1.00 \
  --append-system-prompt "IMPORTANT: Run agent-browser via: $AB <command>. Be efficient. When done, output your final answer clearly." \
  "$TASK_PROMPT" < /dev/null 2>/dev/null)

bench_eval "$RESULT" "$TASK_NAME" "ab" "$TASK_PROMPT" "$OUT_DIR"
