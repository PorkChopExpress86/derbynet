#! /bin/bash
#
# Runs pytest while busy loops load the CPU, the way a flaky browser test is
# reproduced and a fix proved (25+ repeats under load, server/README.md
# "Browser tests").  It keeps every result: pytest's whole output goes to a
# log file and its results to a junit file, with no pipe and no time limit,
# so a long run is never cut short or filtered.  It stops only the loops it
# started.
#
# Usage: server/tools/pytest-under-load.sh [--postgres] [--loops N] [pytest argument ...]
#
#   server/tools/pytest-under-load.sh --postgres -m browser -n 4 --count 25 -k phone ui/tests/browser/test_photos.py
#
# --postgres runs through tools/pytest-postgres.sh.  --loops sets the number
# of busy loops (default 8).  Results go to var/load-runs/<time>/ under
# server/, or DERBYNET_LOAD_RUN_DIR: pytest.log and junit.xml.  At the end it
# prints pytest's summary lines, with -rf's list of failures, and the log's
# path.  It exits with pytest's status.  Run it with the Bash tool's
# run_in_background rather than under `timeout`, which loses the run.

set -e -E -o pipefail

SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER=(uv run pytest)
LOOPS=8
while [ $# -gt 0 ]; do
  case "$1" in
    --postgres) RUNNER=("$SERVER_DIR/tools/pytest-postgres.sh"); shift ;;
    --loops) LOOPS="$2"; shift 2 ;;
    *) break ;;
  esac
done

OUT="${DERBYNET_LOAD_RUN_DIR:-$SERVER_DIR/var/load-runs/$(date +%Y%m%d-%H%M%S)-$$}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

PIDS=()
stop_loops() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap stop_loops EXIT
trap 'exit 143' TERM INT HUP
for _ in $(seq 1 "$LOOPS"); do
  bash -c 'while :; do :; done' &
  PIDS+=("$!")
done

cd "$SERVER_DIR"
echo "Under load ($LOOPS loops): ${RUNNER[*]} $*"
echo "Log: $OUT/pytest.log"
status=0
"${RUNNER[@]}" -rf --junitxml="$OUT/junit.xml" "$@" >"$OUT/pytest.log" 2>&1 || status=$?
stop_loops

# The short test summary (-rf) and the final line, which names an error such
# as a wrong path as well as the counts.
sed -n '/short test summary info/,$p' "$OUT/pytest.log"
grep -q 'short test summary info' "$OUT/pytest.log" || tail -n 5 "$OUT/pytest.log"
echo "pytest exited $status; log: $OUT/pytest.log; junit: $OUT/junit.xml"
exit "$status"
