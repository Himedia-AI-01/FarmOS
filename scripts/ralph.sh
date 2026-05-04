#!/usr/bin/env bash
# Ralph loop runner for FarmOS v2.
# Usage:  bash scripts/ralph.sh [max_iters]
# Stops on Ctrl+C, on RALPH_STUCK.md appearing, or after max_iters (default: infinite).

set -u
cd "$(dirname "$0")/.."

MAX="${1:-0}"          # 0 = infinite
i=0
while :; do
  i=$((i+1))
  if [ "$MAX" != "0" ] && [ "$i" -gt "$MAX" ]; then
    echo "[ralph] reached max iters=$MAX, stopping."
    exit 0
  fi

  if [ -f RALPH_STUCK.md ]; then
    echo "[ralph] RALPH_STUCK.md present, halting. Inspect and delete it to resume."
    exit 1
  fi

  echo "============================================================"
  echo "[ralph] iteration $i  $(date -Iseconds)"
  echo "============================================================"

  claude -p "$(cat PROMPT.md)" \
    --dangerously-skip-permissions \
    --append-system-prompt "You are running Ralph iteration $i. Follow PROMPT.md exactly. Exit after one feature is shipped or blocked." \
    || { echo "[ralph] claude exited non-zero, sleeping 30s"; sleep 30; }

  # Tiny breather so file-system / git settles.
  sleep 5
done
