#!/usr/bin/env bash
# Shared: extract metrics from Claude result, evaluate, and save
# Usage: source this file, then call bench_eval "$RESULT" "$TASK_NAME" "$TOOL" "$TASK_PROMPT" "$OUT_DIR"
#
# Requires: EVAL_PROMPT_TEMPLATE from tasks.sh

bench_eval() {
  local RESULT="$1" TASK_NAME="$2" TOOL="$3" TASK_PROMPT="$4" OUT_DIR="$5"
  local OUT_FILE="$OUT_DIR/${TOOL}_${TASK_NAME}.json"

  local INPUT_TOKENS OUTPUT_TOKENS COST TURNS ANSWER
  INPUT_TOKENS=$(echo "$RESULT" | jq '.usage.input_tokens + .usage.cache_read_input_tokens')
  OUTPUT_TOKENS=$(echo "$RESULT" | jq '.usage.output_tokens')
  COST=$(echo "$RESULT" | jq '.total_cost_usd')
  TURNS=$(echo "$RESULT" | jq '.num_turns')
  ANSWER=$(echo "$RESULT" | jq -r '.result')

  # Evaluate result quality with a second Claude call
  local EVAL_PROMPT EVAL_RESULT EVAL_SCORE EVAL_REASON
  EVAL_PROMPT=$(printf "$EVAL_PROMPT_TEMPLATE" "$TASK_PROMPT" "$ANSWER")
  EVAL_RESULT=$(claude -p \
    --output-format json \
    --json-schema '{"type":"object","properties":{"score":{"type":"number"},"reason":{"type":"string"}},"required":["score","reason"]}' \
    "$EVAL_PROMPT" < /dev/null 2>/dev/null)
  # structured_output is used when --json-schema is specified; .result may be empty
  EVAL_SCORE=$(echo "$EVAL_RESULT" | jq '.structured_output.score // ((.result | fromjson? // empty).score) // 0')
  EVAL_REASON=$(echo "$EVAL_RESULT" | jq -r '.structured_output.reason // ((.result | fromjson? // empty).reason) // "eval failed"')

  jq -n \
    --arg task "$TASK_NAME" \
    --arg tool "$TOOL" \
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
}
