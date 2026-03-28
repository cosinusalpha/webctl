#!/usr/bin/env bash
# Run all benchmarks and produce comparison table
# Usage: ./bench_run.sh [task_numbers...]
# Examples:
#   ./bench_run.sh          # run all 4 tasks
#   ./bench_run.sh 3 4      # run tasks 3 and 4 only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/results/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"

TASKS="${@:-1 2 3 4}"

echo "============================================"
echo "  webctl vs agent-browser benchmark suite"
echo "  Output: $OUT_DIR"
echo "============================================"
echo ""

for TASK in $TASKS; do
  echo ""
  echo "############ TASK $TASK ############"
  echo ""

  # webctl
  bash "$SCRIPT_DIR/bench_webctl.sh" "$TASK" "$OUT_DIR" || echo "WARN: webctl task $TASK failed"
  echo ""

  # agent-browser
  bash "$SCRIPT_DIR/bench_agent_browser.sh" "$TASK" "$OUT_DIR" || echo "WARN: agent-browser task $TASK failed"
  echo ""
done

# Print comparison table
echo ""
echo "============================================"
echo "  RESULTS"
echo "============================================"
echo ""

printf "%-20s %-16s %8s %8s %8s %7s %6s %s\n" \
  "Task" "Tool" "InTok" "OutTok" "Total" "Cost" "Score" "Turns"
printf "%-20s %-16s %8s %8s %8s %7s %6s %s\n" \
  "----" "----" "-----" "------" "-----" "----" "-----" "-----"

for f in "$OUT_DIR"/*.json; do
  [ -f "$f" ] || continue
  printf "%-20s %-16s %8s %8s %8s %7s %6s %s\n" \
    "$(jq -r '.task' "$f")" \
    "$(jq -r '.tool' "$f")" \
    "$(jq -r '.input_tokens' "$f")" \
    "$(jq -r '.output_tokens' "$f")" \
    "$(jq -r '.total_tokens' "$f")" \
    "$(jq -r '.cost_usd | tostring | .[0:7]' "$f")" \
    "$(jq -r '.eval_score' "$f")/10" \
    "$(jq -r '.turns' "$f")"
done

echo ""
echo "Full results in: $OUT_DIR"
