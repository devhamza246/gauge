#!/usr/bin/env bash
# Measure judge-vs-human agreement on the 20 labelled cases, record the judge's replies, and write a report.
#
#   export ANTHROPIC_API_KEY=sk-ant-...      # your key; never commit it
#   bash scripts/calibrate.sh                # about 20 calls to claude-haiku-5-5, a fraction of a cent
#
# Outputs (commit these to make the result verifiable offline):
#   examples/calibration/judge.cassette.jsonl   the judge's recorded replies
#   examples/calibration/REPORT.md              agreement, per-case table, caveats
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is not set. Export it first (it is only read from the environment)." >&2
  exit 2
fi
command -v gauge >/dev/null || { echo "gauge is not installed: pip install -e ." >&2; exit 2; }

CFG=examples/calibration/gauge.yml
LABELS=examples/calibration/labels.jsonl
OUT=examples/calibration/REPORT.md

echo "== live run (records the cassette)"
set +e
gauge calibrate -c "$CFG" --labels "$LABELS" --judge-mode live --out "$OUT"
status=$?
set -e

echo
echo "== offline re-check from the cassette (no API calls)"
gauge calibrate -c "$CFG" --labels "$LABELS" --judge-mode replay | tail -1

echo
echo "Report: $OUT   Cassette: examples/calibration/judge.cassette.jsonl"
exit $status
