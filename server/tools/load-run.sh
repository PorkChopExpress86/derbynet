#! /bin/bash
#
# Serves Django the way production will and runs the race-day load script
# against it (#37), for the Raspberry Pi report.  server/README.md, "Race-day
# load test", describes the clients, the thresholds and the report.
#
# Usage: server/tools/load-run.sh [minutes] [report dir]
#
# Run it from anywhere; it works in server/.  It runs for 30 minutes by
# default and writes var/load-report-<time>/load-report.md (paste it),
# load-report.json and gunicorn.log, under server/ unless a report dir is
# given.  It makes a fresh SQLite database under server/var/, refuses to run
# unless SQLite is in WAL mode, serves it with the locked gunicorn of the
# `serve` dependency group and DERBYNET_LOAD_WORKERS sync workers (default 4,
# a stand-in until M6 settles the production config), runs
# tools/load_test.py --setup at p95 300 ms and p99 1 s, and stops gunicorn.
# It exits with the load script's status: 0 when every check passed.

set -e -E -o pipefail

MINUTES="${1:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SERVER_DIR"
OUT="${2:-var/load-report-$STAMP}"
WORKERS="${DERBYNET_LOAD_WORKERS:-4}"
PORT="${DERBYNET_LOAD_PORT:-8737}"

# The database on local disk, where production keeps DERBYNET_DATA_DIR, not
# in /tmp, which can be a RAM disk.
RUN="$SERVER_DIR/var/load-run-$STAMP"
mkdir -p "$RUN/data" "$RUN/config" "$OUT"
export DERBYNET_DATA_DIR="$RUN/data"
export DERBYNET_CONFIG_DIR="$RUN/config"
export DERBYNET_DEBUG=0
export DATABASE_URL=

GUNICORN_PID=
cleanup() {
  if [ -n "$GUNICORN_PID" ] && kill -0 "$GUNICORN_PID" 2>/dev/null; then
    kill -TERM "$GUNICORN_PID"
    wait "$GUNICORN_PID" 2>/dev/null || true
  fi
  rm -rf "$RUN"
}
trap cleanup EXIT

uv run --locked python manage.py migrate -v0
uv run --locked python manage.py create_test_event >/dev/null
JOURNAL="$(uv run --locked python manage.py shell -v 0 -c \
  "from django.db import connection
c = connection.cursor(); c.execute('PRAGMA journal_mode'); print(c.fetchone()[0])" \
  | tail -1)"
if [ "$JOURNAL" != "wal" ]; then
  echo "SQLite is in journal_mode=$JOURNAL, not wal; the race-day run needs WAL." >&2
  exit 1
fi

GUNICORN_VERSION="$(uv run --locked --group serve gunicorn --version | awk '{print $NF}' | tr -d '()')"
uv run --locked --group serve gunicorn derbynet.wsgi \
  --bind "127.0.0.1:$PORT" --workers "$WORKERS" --pid "$RUN/gunicorn.pid" \
  --access-logfile - --error-logfile - >"$OUT/gunicorn.log" 2>&1 &
GUNICORN_PID=$!
for _ in $(seq 60); do
  curl -sf "http://127.0.0.1:$PORT/action.php?query=role.list" >/dev/null && break
  kill -0 "$GUNICORN_PID" || { cat "$OUT/gunicorn.log"; exit 1; }
  sleep 1
done

STATUS=0
# Sample the gunicorn master and its workers.
uv run --locked python -m tools.load_test \
  --url "http://127.0.0.1:$PORT" --setup \
  --duration "$((MINUTES * 60))" \
  --pid "$(cat "$RUN/gunicorn.pid")" \
  --max-p95-ms 300 --max-p99-ms 1000 \
  --server "gunicorn $GUNICORN_VERSION, $WORKERS sync workers, SQLite journal_mode=$JOURNAL, DEBUG off" \
  --note "The load script ran on the same machine as the server." \
  --markdown "$OUT/load-report.md" --json "$OUT/load-report.json" || STATUS=$?

echo "Report: $OUT/load-report.md"
exit "$STATUS"
