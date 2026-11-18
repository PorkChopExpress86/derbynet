#! /bin/bash
#
# Checks each bound and the thread dumps of race-with-derby-timer.sh (#399):
# runs it once per case, against a server it may reset, and compares how the
# run ended with the case's expected line.  Each run takes one to five
# minutes, so CI does not run this; run it after changing the script.
#
# Usage: race-with-derby-timer-cases.sh <base_url> [<case> ...]
#
# Needs what race-with-derby-timer.sh needs.  The stand-ins go first on the
# PATH.  In timer-stand-in/java, JAVA_DELAY holds the jar's start and
# JAVA_EXIT ends it at once.  In timer-stand-in/curl, TIMER_STATE is the
# timer state every poll.coordinator answer reports.
# Each case's output is kept in $TMPDIR/race-cases.<case>.log.

BASE_URL=$1
shift
TESTING=$(cd "$(dirname "$0")" && pwd)

# heat-result hides the timer's states, so only heat 1's result can count
# as identifying it, and that run must pass.  The result comes 5 s after
# IDENTIFIED (-pace 1, then the 4 s race in SimulatedDevice.HeatRunner.run),
# which came within 4 s of the jar's start here and about 11 s on CI (the
# script's bounds), so IDENTIFY_SECONDS=30 is about twice the 16 s.
#
# name | environment | how the run must end, pass or fail | the line it must print
CASES='
identify-1s|IDENTIFY_SECONDS=1|fail|did not complete round [0-9]*: the timer was not identified within 1s
heat-3s|HEAT_SECONDS=3|fail|did not complete round [0-9]*: no heat finished within 3s of the last
heat-8s|HEAT_SECONDS=8|pass|raced every heat
race-60s|RACE_SECONDS=60|fail|did not complete round [0-9]*: the round did not finish within 60s
race-120s|RACE_SECONDS=120|pass|raced every heat
dump-on-pass|DUMP_AFTER=0|pass|^Full thread dump
slow-start|JAVA_DELAY=151|pass|raced every heat
slow-start-identify-120s|JAVA_DELAY=151 IDENTIFY_SECONDS=120|fail|did not complete round [0-9]*: the timer was not identified within 120s
jar-exits|JAVA_EXIT=1|fail|did not complete round [0-9]*: derby-timer.jar exited
heat-result|TIMER_STATE=1 IDENTIFY_SECONDS=30|pass|heats_run=1 of [0-9]*, timer state 1$
'

STATUS=0
while IFS='|' read -r name env want expect; do
    [ -n "$name" ] || continue
    if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qxF "$name"; then
        continue
    fi
    log="${TMPDIR:-/tmp}/race-cases.$name.log"
    started=$SECONDS
    env $env PATH="$TESTING/timer-stand-in:$PATH" \
        bash "$TESTING/race-with-derby-timer.sh" "$BASE_URL" < /dev/null > "$log" 2>&1
    code=$?
    got=$([ $code = 0 ] && echo pass || echo fail)
    if [ "$got" = "$want" ] && grep -aq -- "$expect" "$log"; then
        verdict=ok
    else
        verdict=WRONG
        STATUS=1
    fi
    echo "$verdict $name ($env): exit $code in $((SECONDS - started))s, $(grep -ao 'TEST FAILURE.*\|raced every heat.*' "$log" | head -1)"
done <<< "$CASES"
exit $STATUS
