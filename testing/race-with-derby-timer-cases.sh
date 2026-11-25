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
# timer state every poll.coordinator answer reports.  In
# timer-stand-in/sleep, FIRST_SLEEP is how long the race loop's first
# `sleep 1` lasts; this script points FIRST_SLEEP_MARK at
# $TMPDIR/race-cases.<case>.first-sleep for it.
# Each case's output is kept in $TMPDIR/race-cases.<case>.log.
#
# in-image runs the jar through extras/docker/ci/timer-java, as
# extras/docker/ci/checks.sh timer does (#518): the timer image's java runs
# the jar built here.  It needs docker and TIMER_IMAGE, the timer image's
# tag, and is skipped without TIMER_IMAGE.  PREFIX (default race-cases)
# names its container.

BASE_URL=$1
shift
TESTING=$(cd "$(dirname "$0")" && pwd)

# heat-result hides the timer's states, so only heat 1's result can count
# as identifying it, and that run must pass.  The last line names the poll
# that identified it.  Heat 1's result comes HEAT_1 seconds after IDENTIFIED
# (-pace 1, then the 4 s race in SimulatedDevice.HeatRunner.run), and so at
# least that long after the jar's start.  IDENTIFIED came within 4 s of the
# jar's start here and about 11 s on CI (the script's bounds), so
# IDENTIFY_SECONDS=30 is about twice the 16 s.
HEAT_1=$((1 + 4))
#
# identify-1s and dump-on-pass hold the loop's first poll to HOLD seconds,
# about twice those 4 s, so the jar is already staging at that poll, and
# each bound must still fire.  dump-on-pass then identifies the timer at
# HOLD seconds or later, which shows the hold took effect.
HOLD=8
#
# name | environment | how the run must end, pass or fail | the line it must
# print | the least `identified after` it may print, if any
CASES="
identify-1s|IDENTIFY_SECONDS=1 FIRST_SLEEP=$HOLD|fail|did not complete round [0-9]*: the timer was not identified within 1s
heat-3s|HEAT_SECONDS=3|fail|did not complete round [0-9]*: no heat finished within 3s of the last
heat-8s|HEAT_SECONDS=8|pass|raced every heat
race-60s|RACE_SECONDS=60|fail|did not complete round [0-9]*: the round did not finish within 60s
race-120s|RACE_SECONDS=120|pass|raced every heat
dump-on-pass|DUMP_AFTER=0 FIRST_SLEEP=$HOLD|pass|^Full thread dump|$HOLD
in-image|JAVA=$TESTING/../extras/docker/ci/timer-java PREFIX=${PREFIX:-race-cases} DUMP_AFTER=0 FIRST_SLEEP=$HOLD|pass|^Full thread dump|$HOLD
slow-start|JAVA_DELAY=151|pass|raced every heat
slow-start-identify-120s|JAVA_DELAY=151 IDENTIFY_SECONDS=120|fail|did not complete round [0-9]*: the timer was not identified within 120s
jar-exits|JAVA_EXIT=1|fail|did not complete round [0-9]*: derby-timer.jar exited
heat-result|TIMER_STATE=1 IDENTIFY_SECONDS=30|pass|identified after [0-9]*s at heats_run=1 of [0-9]*, timer state 1\$|$HEAT_1
"

STATUS=0
while IFS='|' read -r name env want expect least; do
    [ -n "$name" ] || continue
    if [ $# -gt 0 ] && ! printf '%s\n' "$@" | grep -qxF "$name"; then
        continue
    fi
    if [ "$name" = in-image ] && [ -z "${TIMER_IMAGE:-}" ]; then
        echo "skipped $name: set TIMER_IMAGE to the timer image's tag"
        continue
    fi
    log="${TMPDIR:-/tmp}/race-cases.$name.log"
    mark="${TMPDIR:-/tmp}/race-cases.$name.first-sleep"
    started=$SECONDS
    rm -f "$mark"
    env $env FIRST_SLEEP_MARK="$mark" PATH="$TESTING/timer-stand-in:$PATH" \
        bash "$TESTING/race-with-derby-timer.sh" "$BASE_URL" < /dev/null > "$log" 2>&1
    code=$?
    rm -f "$mark"
    got=$([ $code = 0 ] && echo pass || echo fail)
    after=$(grep -ao 'identified after [0-9]*s' "$log" | tail -1 | tr -dc 0-9)
    if [ "$got" = "$want" ] && grep -aq -- "$expect" "$log" \
           && { [ -z "$least" ] || [ "${after:--1}" -ge "$least" ]; }; then
        verdict=ok
    else
        verdict=WRONG
        STATUS=1
    fi
    echo "$verdict $name ($env): exit $code in $((SECONDS - started))s, $(grep -ao 'TEST FAILURE.*\|raced every heat.*' "$log" | head -1)"
done <<< "$CASES"
exit $STATUS
