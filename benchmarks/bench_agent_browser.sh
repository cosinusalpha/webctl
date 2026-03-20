#!/usr/bin/env bash
# Benchmark: run a task with Claude + agent-browser
# Usage: ./bench_agent_browser.sh <task_number> [output_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/tasks.sh"

TASK_NUM="${1:?Usage: $0 <task_number 1-4> [output_dir]}"
OUT_DIR="${2:-$SCRIPT_DIR/results}"
mkdir -p "$OUT_DIR"

AB="$SCRIPT_DIR/agent-browser/node_modules/.bin/agent-browser"

# Get task name and prompt
TASK_NAME_VAR="TASK${TASK_NUM}_NAME"
TASK_PROMPT_VAR="TASK${TASK_NUM}_PROMPT"
TASK_NAME="${!TASK_NAME_VAR}"
TASK_PROMPT="${!TASK_PROMPT_VAR}"

if [[ -z "$TASK_NAME" ]]; then
  echo "ERROR: Unknown task $TASK_NUM" >&2
  exit 1
fi

OUT_FILE="$OUT_DIR/ab_${TASK_NAME}.json"

echo "=== agent-browser benchmark: $TASK_NAME ==="
echo "Task: $TASK_PROMPT"
echo ""

# Run Claude with agent-browser tools
SYSTEM_PROMPT="You are benchmarking agent-browser (Vercel). Use ONLY these bash commands to complete the task:
- $AB open '<url>'
- $AB snapshot
- $AB click '<selector_or_@ref>'
- $AB type '<selector_or_@ref>' '<text>'
- $AB press <key>
- $AB wait <ms>
- $AB scroll '<direction>'
- $AB get text '<selector_or_@ref>'

The snapshot command returns the full a11y tree. Elements have [ref=eN] references you can use for click/type.
Be efficient. When done, output your final answer clearly."

RESULT=$(claude -p \
  --output-format json \
  --allowedTools "Bash($AB *)" \
  --permission-mode bypassPermissions \
  --max-budget-usd 1.00 \
  -s "$SYSTEM_PROMPT" \
  "$TASK_PROMPT" 2>/dev/null)

# Extract metrics
INPUT_TOKENS=$(echo "$RESULT" | jq '.usage.input_tokens + .usage.cache_read_input_tokens')
OUTPUT_TOKENS=$(echo "$RESULT" | jq '.usage.output_tokens')
COST=$(echo "$RESULT" | jq '.total_cost_usd')
TURNS=$(echo "$RESULT" | jq '.num_turns')
ANSWER=$(echo "$RESULT" | jq -r '.result')

# Evaluate result quality
EVAL_PROMPT=$(printf "$EVAL_PROMPT_TEMPLATE" "$TASK_PROMPT" "$ANSWER")
EVAL_RESULT=$(claude -p \
  --output-format json \
  --json-schema '{"type":"object","properties":{"score":{"type":"number"},"reason":{"type":"string"}},"required":["score","reason"]}' \
  "$EVAL_PROMPT" 2>/dev/null)
EVAL_SCORE=$(echo "$EVAL_RESULT" | jq -r '.result' | jq '.score')
EVAL_REASON=$(echo "$EVAL_RESULT" | jq -r '.result' | jq -r '.reason')

# Build output
jq -n \
  --arg task "$TASK_NAME" \
  --arg tool "agent-browser" \
  --arg prompt "$TASK_PROMPT" \
  --argjson input_tokens "$INPUT_TOKENS" \
  --argjson output_tokens "$OUTPUT_TOKENS" \
  --argjson cost "$COST" \
  --argjson turns "$TURNS" \
  --arg answer "$ANSWER" \
  --argjson eval_score "$EVAL_SCORE" \
  --arg eval_reason "$EVAL_REASON" \
  '{task: $task, tool: $tool, prompt: $prompt, input_tokens: $input_tokens, output_tokens: $output_tokens, total_tokens: ($input_tokens + $output_tokens), cost_usd: $cost, turns: $turns, answer: $answer, eval_score: $eval_score, eval_reason: $eval_reason}' \
  > "$OUT_FILE"

echo ""
echo "--- Result ---"
echo "Tokens: in=$INPUT_TOKENS out=$OUTPUT_TOKENS total=$(($INPUT_TOKENS + $OUTPUT_TOKENS))"
echo "Cost: \$$COST | Turns: $TURNS"
echo "Quality: $EVAL_SCORE/10 — $EVAL_REASON"
echo "Saved: $OUT_FILE"
