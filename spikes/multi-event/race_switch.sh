#!/usr/bin/env bash
# Option (a): two simultaneous switches to the same event that sits at migration 0001.
# Usage: race_switch.sh DB TRIALS
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
db=$1 trials=$2 port=${PORT:-8091} venv=${VENV:-$here/.venv}
export SPIKE_MODE=a SPIKE_DB=$db SPIKE_DATA=${SPIKE_DATA:-$here/.data/race-$db}
cd "$here"
"$venv/bin/python" -m spike.setup e1 100 >/dev/null
slugs=$(seq -f "old%g" 1 "$trials" | paste -sd,)
"$venv/bin/python" -m spike.make_old "$slugs" 1000
"$venv/bin/gunicorn" -w 4 -b 127.0.0.1:$port spike.wsgi:application --log-level critical &
gpid=$!
trap 'kill $gpid 2>/dev/null; wait $gpid 2>/dev/null || true' EXIT
for _ in $(seq 100); do curl -sf "http://127.0.0.1:$port/kiosk/poll" >/dev/null && break; sleep 0.1; done
for slug in ${slugs//,/ }; do
  pids=()
  for _ in 1 2; do curl -s -o /dev/null -w "%{http_code} " -X POST "http://127.0.0.1:$port/switch?to=$slug" & pids+=($!); done
  wait "${pids[@]}"
  echo "$slug $(curl -s "http://127.0.0.1:$port/kiosk/poll" | python3 -c 'import json,sys; print("kiosk now serves", json.load(sys.stdin)["event"])')"
done
