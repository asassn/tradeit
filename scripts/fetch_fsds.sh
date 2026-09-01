#!/usr/bin/env bash
# Download the SEC Financial Statement Data Sets (milestone 5's input).
#
# These are the quarterly ZIPs behind EDGAR's XBRL filings -- sub.txt, num.txt,
# tag.txt, pre.txt -- keyed on the accession number (`adsh`). They begin in
# 2009 and are the free, authoritative source for as-filed statement values.
#
# SEC FAIR ACCESS: a descriptive User-Agent carrying real contact details is
# REQUIRED. Without one sec.gov returns 403, which was confirmed by measurement
# rather than read from the docs. This script therefore refuses to run rather
# than sending a placeholder, on the same principle as the rest of the project:
# an identifier nobody can check is not provenance.
#
#   export EDGAR_USER_AGENT="Your Name your.email@example.com"
#   ./scripts/fetch_fsds.sh 2009 2026
#
# Landing page (authoritative index of what exists):
#   https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets
set -euo pipefail

if [[ -z "${EDGAR_USER_AGENT:-}" ]]; then
  echo "EDGAR_USER_AGENT is not set." >&2
  echo 'export EDGAR_USER_AGENT="Your Name your.email@example.com"' >&2
  exit 1
fi

START_YEAR="${1:-2009}"
END_YEAR="${2:-$(date +%Y)}"
DEST="${FSDS_DIR:-$HOME/Documents/TradeItData/edgar/fsds}"
BASE="https://www.sec.gov/files/dera/data/financial-statement-data-sets"

mkdir -p "$DEST"
echo "destination : $DEST"
echo "range       : ${START_YEAR}Q1 - ${END_YEAR}Q4"
echo

missing=0
for year in $(seq "$START_YEAR" "$END_YEAR"); do
  for q in 1 2 3 4; do
    name="${year}q${q}.zip"
    target="$DEST/$name"
    if [[ -s "$target" ]]; then
      echo "have    $name"
      continue
    fi
    # --fail so a 403 or 404 is an error rather than a saved error page. A
    # quarter that does not exist yet is normal and is counted, not fatal.
    if curl -sS --fail --max-time 300 -A "$EDGAR_USER_AGENT" -o "$target.part" "$BASE/$name"; then
      mv "$target.part" "$target"
      echo "fetched $name  ($(wc -c <"$target" | tr -d ' ') bytes)"
    else
      rm -f "$target.part"
      echo "absent  $name"
      missing=$((missing + 1))
    fi
    # SEC fair-access: stay well under 10 requests/second.
    sleep 0.5
  done
done

echo
echo "done. $(ls -1 "$DEST"/*.zip 2>/dev/null | wc -l | tr -d ' ') quarters present, $missing not available."
echo "An absent recent quarter is normal -- the SEC publishes a quarter some weeks after it ends."
