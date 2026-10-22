#!/usr/bin/env bash
# Usage: run_one.sh MODE DB OUT_PREFIX [loadgen args...]
# Env: VENV (default .venv), PORT (8091), SPIKE_DATA, SPIKE_FAULT, SPIKE_PG_PORT, GUNICORN_ARGS ("-w 4")
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mode=$1 db=$2 out=$3; shift 3
venv=${VENV:-$here/.venv}
port=${PORT:-8091}
export SPIKE_MODE=$mode SPIKE_DB=$db SPIKE_DATA=${SPIKE_DATA:-$here/.data/$mode-$db}
cd "$here"
"$venv/bin/python" -m spike.setup e1,e2,e3 100 >/dev/null
"$venv/bin/gunicorn" ${GUNICORN_ARGS:--w 4} -b 127.0.0.1:$port spike.wsgi:application \
  --error-logfile "$out.gunicorn.log" --log-level warning &
gpid=$!
trap 'kill $gpid 2>/dev/null; wait $gpid 2>/dev/null || true' EXIT
for _ in $(seq 100); do curl -sf "http://127.0.0.1:$port/kiosk/poll" >/dev/null && break; sleep 0.1; done
"$venv/bin/python" loadgen.py "http://127.0.0.1:$port" "$out.jsonl" "$@" | tee "$out.summary.json"
