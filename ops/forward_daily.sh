#!/bin/bash
# The daily forward capture. Approved by the owner 2026-10-20.
#
# **A trailing window, not yesterday alone.** A laptop asleep at 22:10 misses
# its run, and launchd does not replay a missed StartCalendarInterval. Asking
# about the last --lookback sessions every day means a missed day is recovered
# by the next run that happens, with no catch-up logic and no state to go
# wrong: the capture is idempotent on prices, so re-asking costs credits and
# writes nothing new.
#
# That is the whole reliability design. A daily job that only ever asked about
# yesterday would lose a day permanently every time the machine slept, and lose
# it silently.
set -euo pipefail

REPO="/Users/ericsasson/Documents/GitHub/TradeIt"
cd "$REPO"

# The credentials live in .env, which is git-ignored. **Parsed, never sourced.**
#
# `. ./.env` executes the file as shell, and this one failed on it: a value
# containing parentheses is shell syntax, so bash reported a syntax error on
# line 2 and the job exited having captured nothing. An earlier comment here
# claimed the quoting was handled; it was not, and the clean-environment test
# is what found it.
#
# Executing a config file is also how a value containing $(...) becomes a
# command. Reading key=value line by line makes the contents data, which is
# what they are.
while IFS='=' read -r key value; do
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  export "$key=$value"
done < <(grep -v '^[[:space:]]*#' .env | grep '=')

OUT="/Users/ericsasson/Documents/TradeItData/out"
TICKERS="$(tr -d '\n' < ops/forward_universe.txt)"

exec .venv/bin/python scripts/forward_capture.py \
  --tickers "$TICKERS" \
  --lookback 7 \
  --record "$OUT/forward.sqlite" \
  --journal "$OUT/forward_journal.jsonl"
