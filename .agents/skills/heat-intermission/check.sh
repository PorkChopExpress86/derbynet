#!/usr/bin/env bash
# Check all invariants of HeatIntermission.tla individually with pinned TLC v1.7.4.
# Usage:
#   ./.agents/skills/heat-intermission/check.sh [INVARIANT ...]
# Set TLA2TOOLS to override the jar path, or let this script cache v1.7.4 under ~/.local/share/tla2tools/.
# Each invariant that passes is checked against every "N distinct states" and
# "all N states" figure in SKILL.md, and a figure TLC did not find fails the run:
# #515's body and log gave 328,096 while SKILL.md still said 190,080 in six
# places (#515 retro).
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
    NoRacingDuringIntermission
  )
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$DIR/HeatIntermission.tla" "$WORK/HeatIntermission.tla"

status=0
counts=()
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
    # The total is TLC's one count outside its Progress(...) lines, which
    # print counts with commas before the run ends.
    total="$(grep -v '^Progress(' "$out" | grep -oE '[0-9][0-9,]* distinct states found' || true)"
    total="${total%% *}"
    if [ -z "$total" ]; then
      echo "NOCOUNT  ${inv}: TLC printed no 'distinct states found' line, so SKILL.md's counts go unchecked."
      status=1
    else
      counts+=("${total//,/}")
    fi
  else
    echo "FAIL  ${inv}"
    grep -E 'Error:|State [0-9]+:|distinct states found' "$out" | sed -n '1,30p'
    status=1
  fi
done

for found in $(printf '%s\n' "${counts[@]}" | sort -u); do
  while IFS=: read -r line figure; do
    said="${figure#all }"
    said="${said%% *}"
    if [ "${said//,/}" != "$found" ]; then
      echo "STALE  SKILL.md:${line} says ${figure}; TLC found ${found} distinct states." \
           "Update the baseline table (step 4 of Updating the model)."
      status=1
    fi
  done < <(grep -noE '[0-9][0-9,]* distinct states|all [0-9][0-9,]* states' "$DIR/SKILL.md" || true)
done

exit "$status"
