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
# --postgres runs through tools/pytest-postgres.sh, and starts the loops once
# its Postgres is ready: started under load, it once missed a 300 s wait
# (#54 retro).  --loops sets the number
# of busy loops (default 8).  Results go to var/load-runs/<time>/ under
# server/, or DERBYNET_LOAD_RUN_DIR, which must be new or empty: pytest.log
# and junit.xml.  At the end it prints pytest's summary lines, with -rf's
# list of failures, and the log's path.  It exits with pytest's status.  Run it with the Bash tool's
# run_in_background rather than under `timeout`, which loses the run.

set -e -E -o pipefail

# It refuses to start through a pipe or under `timeout`: #47 and #48 each
# lost a 25-repeat run to `timeout 3000 ... | grep`, killed with nothing
# kept (#48 retro).
refuse() {
  echo "pytest-under-load.sh: $1. Run it with the Bash tool's run_in_background," \
       "with no pipe and no timeout, and read the summary it prints." >&2
  exit 2
}
[ -p /dev/stdout ] && refuse "its output is a pipe"
pid=$PPID
while [ "${pid:-0}" -gt 1 ]; do
  [ "$(ps -o comm= -p "$pid" || true)" = timeout ] && refuse "it runs under timeout"
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ') || pid=1
done

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
# A folder that already holds files is refused: #220 reused one, and a wait
# for its junit.xml fired at once on an earlier session's results (#220
# retro).  Wait on this script's own last line, "pytest exited N".
if [ -d "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
  echo "pytest-under-load.sh: $OUT already holds files. Name a folder that" \
       "does not exist yet, or leave DERBYNET_LOAD_RUN_DIR unset." >&2
  exit 2
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

PIDS=()
RUN_PID=
stop_loops() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  PIDS=()
}
stop_all() {
  stop_loops
  if [ -n "$RUN_PID" ]; then kill "$RUN_PID" 2>/dev/null || true; fi
}
trap stop_all EXIT
trap 'exit 143' TERM INT HUP

cd "$SERVER_DIR"
echo "Under load ($LOOPS loops): ${RUNNER[*]} $*"
echo "Log: $OUT/pytest.log"
READY="$OUT/postgres-ready"
rm -f "$READY"
DERBYNET_PG_READY_FILE="$READY" \
  "${RUNNER[@]}" -rf --junitxml="$OUT/junit.xml" "$@" >"$OUT/pytest.log" 2>&1 &
RUN_PID=$!
if [ "${RUNNER[0]}" != uv ]; then
  while [ ! -e "$READY" ] && kill -0 "$RUN_PID" 2>/dev/null; do sleep 1; done
fi
for _ in $(seq 1 "$LOOPS"); do
  bash -c 'while :; do :; done' &
  PIDS+=("$!")
done
status=0
wait "$RUN_PID" || status=$?
RUN_PID=
stop_loops

# The short test summary (-rf) and the final line, which names an error such
# as a wrong path as well as the counts.
sed -n '/short test summary info/,$p' "$OUT/pytest.log"
grep -q 'short test summary info' "$OUT/pytest.log" || tail -n 5 "$OUT/pytest.log"
echo "pytest exited $status; log: $OUT/pytest.log; junit: $OUT/junit.xml"
exit "$status"
