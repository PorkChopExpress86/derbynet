#!/bin/bash
# SPIKE #71: the fixture database both prototypes run against, built the way
# .github/workflows/server.yml builds it: the roster, the den changes, then
# test-master-schedule.sh, which races every first round (and a Grand Finals)
# through timer-message.  White's Wolves ends with 13 ranked racers, the class
# the standings reveal steps through; Lions & Tigers is then unraced again
# (master schedule off) for the now-racing heats the scripts drive.
#   prototypes/kiosk-spike/tools/fixture.sh http://127.0.0.1:8771
set -e -o pipefail
BASE_URL="${1:-http://127.0.0.1:8771}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
bash testing/reset-database.sh "$BASE_URL" >/dev/null
bash testing/import-roster.sh "$BASE_URL" >/dev/null
bash testing/test-den-changes.sh "$BASE_URL" >/dev/null
bash testing/test-master-schedule.sh "$BASE_URL" >/dev/null
node "$ROOT/prototypes/kiosk-spike/tools/driver.js" reset-lions "$BASE_URL"
echo "fixture loaded into $BASE_URL"
