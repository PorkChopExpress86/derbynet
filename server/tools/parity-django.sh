#! /bin/bash
#
# Starts a Django server from this checkout the way .github/workflows/server.yml
# does (runserver, test endpoints on, a fresh database and no event), and runs
# the parity gate's Django leg against it: testing/docker/run-suite.sh parity,
# from the harness image, with DERBYNET_BACKEND=django.  The parity gate (#37)
# runs it; see server/tools/parity_gate.py.
#
# Usage: server/tools/parity-django.sh <leg-dir> [port]
#
# DATABASE_URL picks Postgres; unset, the server uses SQLite in its data folder.
# The data folder is <leg-dir>/data, or DERBYNET_PARITY_DATA_DIR, which CI sets to
# /var/lib/derbynet, where the PHP image keeps its data, so that the folder
# settings of the two servers differ only as D57 says.
# The harness image must be built (run-suite.sh harness), with the same
# DERBYNET_TEST_PREFIX.  The server's log is <leg-dir>/server.log.

set -e -o pipefail

LEG="${1:?usage: parity-django.sh <leg-dir> [port]}"
PORT="${2:-8080}"
SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$LEG"
LEG="$(cd "$LEG" && pwd)"

export DERBYNET_ENABLE_TEST_ENDPOINTS=1
export DERBYNET_DATA_DIR="${DERBYNET_PARITY_DATA_DIR:-$LEG/data}"
export DERBYNET_CONFIG_DIR="$LEG/config"
# Emptied, not removed: CI's /var/lib/derbynet sits in a folder it can't write.
mkdir -p "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR"
find "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR" -mindepth 1 -delete

cd "$SERVER_DIR"
uv run python manage.py migrate --verbosity 0
uv run python manage.py runserver "127.0.0.1:$PORT" --noreload \
    > "$LEG/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

for _ in $(seq 60); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/health/" && break
    sleep 1
done

# run-suite.sh parity replaces the leg's steps and copy of testing/ only.
DERBYNET_BACKEND=django DERBYNET_PARITY_DIR="$LEG/suite" \
    DERBYNET_PARITY_URL="http://127.0.0.1:$PORT" \
    "$SERVER_DIR/../testing/docker/run-suite.sh" parity
