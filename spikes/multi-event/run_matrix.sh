#!/usr/bin/env bash
# Full load matrix, configs interleaved per rep. Usage: run_matrix.sh OUT_DIR [REPS]
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out=$1 reps=${2:-5}
mkdir -p "$out"
for rep in $(seq 1 "$reps"); do
  for cfg in a:sqlite b:sqlite a:pg b:pg; do
    mode=${cfg%%:*} db=${cfg##*:}
    echo "rep $rep $mode-$db $(date +%T)" >&2
    "$here/run_one.sh" "$mode" "$db" "$out/$mode-$db-r$rep" >/dev/null 2>>"$out/stderr.log"
  done
done
