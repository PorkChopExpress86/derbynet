#!/bin/bash
# SPIKE #71: every measurement in RESULTS.md, in order, against a server that
# serve.sh started.  About 40 minutes.  Needs Node with Playwright (NODE_PATH)
# and xvfb-run for the headed runs.
#   prototypes/kiosk-spike/tools/run-all.sh [base]
set -e -o pipefail
BASE="${1:-http://127.0.0.1:8771}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
mkdir -p results
XVFB=(xvfb-run -a -s "-screen 0 1920x1080x24")
tools/fixture.sh "$BASE"
node tools/step-anims.js "$BASE" | tee results/step-anims.txt
"${XVFB[@]}" node tools/trace.js "$BASE" 1 4 | tee results/trace-run.log
node tools/replay-check.js "$BASE" | tee results/replay-run.log
CANDS=a,b "${XVFB[@]}" node tools/record.js "$BASE" | tee results/record-run.log
node tools/sync.js "$BASE" | tee results/sync-run.log
python3 -I tools/loc.py > results/loc.json
echo "done"
