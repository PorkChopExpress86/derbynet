#! /bin/bash
#
# Checks each bound and the thread dumps of race-with-derby-timer.sh (#399):
# runs it once per case, against a server it may reset, and compares how the
# run ended with the case's expected line.  Each run takes one to five
# minutes, so CI does not run this; run it after changing the script.
#
# Usage: race-with-derby-timer-cases.sh <base_url> [<case> ...]
#
# Needs what race-with-derby-timer.sh needs.  timer-stand-in/java goes first
# on the PATH: JAVA_DELAY holds the jar's start, JAVA_EXIT ends it at once.
# Each case's output is kept in $TMPDIR/race-cases.<case>.log.

BASE_URL=$1
shift
TESTING=$(cd "$(dirname "$0")" && pwd)

# name | environment | the line the run must print
CASES='
identify-1s|IDENTIFY_SECONDS=1|did not complete round [0-9]*: the timer was not identified within 1s
heat-3s|HEAT_SECONDS=3|did not complete round [0-9]*: no heat finished within 3s of the last
heat-8s|HEAT_SECONDS=8|raced every heat
race-60s|RACE_SECONDS=60|did not complete round [0-9]*: the round did not finish within 60s
race-120s|RACE_SECONDS=120|raced every heat
dump-on-pass|DUMP_AFTER=0|^Full thread dump
slow-start|JAVA_DELAY=151|raced every heat
slow-start-identify-120s|JAVA_DELAY=151 IDENTIFY_SECONDS=120|did not complete round [0-9]*: the timer was not identified within 120s
jar-exits|JAVA_EXIT=1|did not complete round [0-9]*: derby-timer.jar exited
'

STATUS=0
while IFS='|' read -r name env expect; do
    [ -n "$name" ] || continue
    if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qxF "$name"; then
        continue
    fi
    log="${TMPDIR:-/tmp}/race-cases.$name.log"
    started=$SECONDS
    env $env PATH="$TESTING/timer-stand-in:$PATH" \
        bash "$TESTING/race-with-derby-timer.sh" "$BASE_URL" < /dev/null > "$log" 2>&1
    code=$?
    # A pass must exit 0; a failure must exit non-zero with its own line.
    case "$expect" in
        "raced every heat"|"^Full thread dump") want=0 ;;
        *) want=1 ;;
    esac
    got=$([ $code = 0 ] && echo 0 || echo 1)
    if [ "$got" = "$want" ] && grep -aq -- "$expect" "$log"; then
        verdict=ok
    else
        verdict=WRONG
        STATUS=1
    fi
    echo "$verdict $name ($env): exit $code in $((SECONDS - started))s, $(grep -ao 'TEST FAILURE.*\|raced every heat.*' "$log" | head -1)"
done <<< "$CASES"
exit $STATUS
