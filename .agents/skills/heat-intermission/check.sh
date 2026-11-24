#!/usr/bin/env bash
# Check all invariants of HeatIntermission.tla individually with pinned TLC v1.7.4.
# Usage:
#   ./.agents/skills/heat-intermission/check.sh [INVARIANT ...]
# Set TLA2TOOLS to override the jar path, or let this script cache v1.7.4 under ~/.local/share/tla2tools/.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
TLA_VERSION="v1.7.4"
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/${TLA_VERSION}/tla2tools.jar"
TLA2TOOLS="${TLA2TOOLS:-$HOME/.local/share/tla2tools/tla2tools-1.7.4.jar}"
JAVA="${JAVA:-java}"

if [ ! -f "$TLA2TOOLS" ]; then
  mkdir -p "$(dirname "$TLA2TOOLS")"
  echo "Downloading tla2tools.jar (${TLA_VERSION}) to ${TLA2TOOLS}..." >&2
  curl -fsSL -o "$TLA2TOOLS" "$TLA_URL"
fi

if [ "$#" -gt 0 ]; then
  INVARIANTS=("$@")
else
  INVARIANTS=(
    RacingHeatAllowed
    WaitingFinalMatchesStandings
    NoIntermissionAfterFinalRaces
    LockedFeederProtected
  )
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$DIR/HeatIntermission.tla" "$WORK/HeatIntermission.tla"

status=0
for inv in "${INVARIANTS[@]}"; do
  cfg="${inv}.cfg"
  printf "SPECIFICATION Spec\nINVARIANT %s\n" "$inv" > "$WORK/$cfg"
  out="$WORK/${inv}.out"
  if (cd "$WORK" && "$JAVA" -XX:+UseParallelGC -cp "$TLA2TOOLS" tlc2.TLC \
      -metadir "$WORK/meta-${inv}" \
      -config "$cfg" \
      -workers auto \
      HeatIntermission.tla) > "$out" 2>&1; then
    states="$(grep -E 'distinct states found' "$out" | tail -n 1 || true)"
    echo "PASS  ${inv}  (${states})"
  else
    echo "FAIL  ${inv}"
    grep -E 'Error:|State [0-9]+:|distinct states found' "$out" | sed -n '1,30p'
    status=1
  fi
done

exit "$status"
