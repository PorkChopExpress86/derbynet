#!/bin/bash
# Serves Django the way production will and runs the race-day load script
# against it (#37).  Run it from server/ on the machine to measure:
#
#   tools/load-run.sh [minutes] [report dir]
#
# Defaults: 30 minutes, and the report in var/load-report-<time>/ as
# load-report.md (paste it) and load-report.json.  It makes a fresh SQLite
# database in WAL mode under var/, starts gunicorn on it with
# DERBYNET_LOAD_WORKERS sync workers (default 4, one per Pi 4 core), runs
# tools/load_test.py with --setup at the #37 thresholds (p95 300 ms, p99
# 1 s), samples gunicorn's CPU and memory, and stops gunicorn.  It exits with
# the load script's status: 0 when every check passed.
#
# gunicorn comes from `uv run --with gunicorn`, so the first run needs to
# reach PyPI; the server's own dependencies stay as uv.lock pins them.

set -euo pipefail

MINUTES=${1:-30}
STAMP=$(date +%Y%m%d-%H%M%S)
cd "$(dirname "$0")/.."
OUT=${2:-var/load-report-$STAMP}
WORKERS=${DERBYNET_LOAD_WORKERS:-4}
PORT=${DERBYNET_LOAD_PORT:-8737}

# The database on local disk, as production keeps DERBYNET_DATA_DIR, not in
# /tmp, which can be a RAM disk.
RUN=var/load-run-$STAMP
mkdir -p "$RUN/data" "$RUN/config" "$OUT"
export DERBYNET_DATA_DIR=$PWD/$RUN/data
export DERBYNET_CONFIG_DIR=$PWD/$RUN/config
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
JOURNAL=$(uv run --locked python manage.py shell -v 0 -c \
    "from django.db import connection
c = connection.cursor(); c.execute('PRAGMA journal_mode'); print(c.fetchone()[0])" | tail -1)

uv run --locked --with gunicorn gunicorn derbynet.wsgi \
    --bind "127.0.0.1:$PORT" --workers "$WORKERS" --pid "$RUN/gunicorn.pid" \
    --access-logfile - --error-logfile - >"$OUT/gunicorn.log" 2>&1 &
GUNICORN_PID=$!
for _ in $(seq 60); do
    curl -sf "http://127.0.0.1:$PORT/action.php?query=role.list" >/dev/null && break
    kill -0 "$GUNICORN_PID" || { cat "$OUT/gunicorn.log"; exit 1; }
    sleep 1
done
# Sample the gunicorn master and its workers.
MASTER=$(cat "$RUN/gunicorn.pid")

STATUS=0
uv run --locked python -m tools.load_test \
    --url "http://127.0.0.1:$PORT" --setup \
    --duration $((MINUTES * 60)) \
    --pid "$MASTER" \
    --max-p95-ms 300 --max-p99-ms 1000 \
    --server "gunicorn $(uv run --locked --with gunicorn gunicorn --version | awk '{print $NF}' | tr -d '()'), $WORKERS sync workers, SQLite journal_mode=$JOURNAL, DEBUG off" \
    --note "The load script ran on the same machine as the server." \
    --markdown "$OUT/load-report.md" --json "$OUT/load-report.json" || STATUS=$?

echo "Report: $OUT/load-report.md"
exit $STATUS
