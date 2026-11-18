#! /bin/bash
#
# Races one scheduled round with the real derby-timer.jar and its simulated
# timer, the way timer/testing/fake-timer runs it, and fails unless every heat
# gets a result (#32).
#
# Usage: race-with-derby-timer.sh <base_url> [<derby-timer.jar>]
#
# Build the jar first with `ant -noinput -buildfile timer/build.xml`.  Needs
# java on the path.  Starts from an empty database.
#
# The jar's output goes to testing/derby-timer.log, and its own log, with a
# millisecond time on every line and every heartbeat traced (-th), to
# testing/timer-*.log.  The script prints a timeline of the round's progress
# as it polls.  A jar that has not identified its timer after DUMP_AFTER
# seconds gets a thread dump (SIGQUIT) in derby-timer.log, and a failure takes
# two more and prints both logs (#399).

BASE_URL=$1
JAR=${2:-`dirname $0`/../timer/dist/lib/derby-timer.jar}
set -e -E -o pipefail
source `dirname $0`/common.sh

# The bounds come from the step's last 30 CI runs before #399 (shell group 3,
# both databases): the whole race took 88.5 to 95.7 s for 17 heats at the
# simulated timer's 5 s a heat (-pace 1, then SimulatedDevice's 4 s race).
# In the one failure (run 44181424925), the jar first reported to Django
# 151 s after it started, then raced heats 1 to 6 at the usual 5 s each, and
# one 180 s deadline for the whole round ended the run 4 s after heat 7
# started.
#
# IDENTIFY_SECONDS bounds the wait for the jar to identify its timer, about
# twice that 151 s.
IDENTIFY_SECONDS=${IDENTIFY_SECONDS:-300}
# HEAT_SECONDS bounds each heat once the timer is identified: six times the
# 5 s a heat takes.
HEAT_SECONDS=${HEAT_SECONDS:-30}
# A thread dump after this long, if the timer is not identified yet; the wait
# goes on.
DUMP_AFTER=${DUMP_AFTER:-30}

TIMER_LOG="`dirname $0`/derby-timer.log"

user_login_coordinator
`dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"
user_login_coordinator
curl_postj action.php "action=settings.write&n-lanes=4&unused-lane-mask=0" | check_jsuccess
curl_postj action.php "action=racer.bulk&who=all&what=checkin" | check_jsuccess
ROUNDID=$(curl_getj "action.php?query=poll.coordinator" | jq '.rounds[0].roundid')
curl_postj action.php "action=schedule.generate&roundid=$ROUNDID" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$ROUNDID&now_racing=1" | check_jsuccess
HEATS=$(NOANNOUNCE=1 curl_getj "action.php?query=poll.coordinator" \
            | jq --argjson r "$ROUNDID" '.rounds[] | select(.roundid == $r) | .heats_scheduled')

stamp() {
    date -u +%H:%M:%S.%3N
}

rm -f "`dirname $0`"/timer-*.log
: > "$TIMER_LOG"
{ echo "$(stamp) uptime: $(uptime)"; java -version 2>&1; } >&2
java -jar "$JAR" -x -t -th -r -logdir "`dirname $0`" -simulate-timer -lanes 4 -pace 1 \
     "$BASE_URL" >> "$TIMER_LOG" 2>&1 &
JAR_PID=$!
START=$SECONDS
trap 'kill $JAR_PID 2>/dev/null || true' EXIT

# SIGQUIT makes the JVM print every thread's stack to its stdout, the jar's
# log.  Sent only to the java this script started.
thread_dump() {
    local ppid
    ppid=$(awk '/^PPid:/ { print $2 }' /proc/$JAR_PID/status 2>/dev/null) || true
    if [ "$ppid" = "$$" ] && [ "$(cat /proc/$JAR_PID/comm 2>/dev/null)" = java ]; then
        echo "===== $(stamp) thread dump: $1" >> "$TIMER_LOG"
        kill -QUIT $JAR_PID
        sleep 1
    else
        echo "$(stamp) no thread dump: pid $JAR_PID is not this script's java" >&2
    fi
}

# Prints "<heats_run> <timer state>", such as "6 4".
progress() {
    NOANNOUNCE=1 curl_getj "action.php?query=poll.coordinator" \
        | jq -r --argjson r "$ROUNDID" \
             '"\(.rounds[] | select(.roundid == $r) | .heats_run) \(.["timer-state"].state)"'
}

finish() {
    curl_getj "action.php?query=poll.coordinator" | tee /dev/stderr \
        | jq -e --argjson r "$ROUNDID" \
             '.rounds[] | select(.roundid == $r) | .heats_scheduled > 0 and .heats_run == .heats_scheduled' \
             >/dev/null && return 0
    thread_dump "$1"
    sleep 2
    thread_dump "$1, again"
    cat "$TIMER_LOG"
    for f in "`dirname $0`"/timer-*.log; do
        echo "===== $f"
        cat "$f"
    done
    test_fails "derby-timer.jar did not complete round $ROUNDID: $1"
}

# The timer counts as identified once Django has it connected (2), staging
# (3) or racing (4), the states IDENTIFIED and the heats leave it in
# (racing/services/timer.py), or once a heat has a result.
LAST=""
IDENTIFIED_AT=""
DUMPED=""
while true; do
    sleep 1
    NOW=$((SECONDS - START))
    read -r RUN STATE <<< "$(progress)"
    [ "$RUN $STATE" = "$LAST" ] \
        || echo "$(stamp) +${NOW}s heats_run=$RUN of $HEATS, timer state $STATE" >&2
    LAST="$RUN $STATE"
    if [ -z "$IDENTIFIED_AT" ]; then
        if [ "$RUN" -gt 0 ] || [ "$STATE" = 2 ] || [ "$STATE" = 3 ] || [ "$STATE" = 4 ]; then
            IDENTIFIED_AT=$NOW
            LAST_HEAT_AT=$NOW
            LAST_RUN=$RUN
        elif [ $NOW -ge $IDENTIFY_SECONDS ]; then
            finish "the timer was not identified within ${IDENTIFY_SECONDS}s"
        elif [ $NOW -ge $DUMP_AFTER ] && [ -z "$DUMPED" ]; then
            DUMPED=1
            thread_dump "timer not identified after ${NOW}s"
        fi
        continue
    fi
    [ "$RUN" -ge "$HEATS" ] && break
    if [ "$RUN" -gt "$LAST_RUN" ]; then
        LAST_RUN=$RUN
        LAST_HEAT_AT=$NOW
    elif [ $((NOW - LAST_HEAT_AT)) -ge $HEAT_SECONDS ]; then
        finish "no heat finished within ${HEAT_SECONDS}s of the last, after $RUN"
    fi
done

finish "the last poll"
echo "derby-timer.jar raced every heat of round $ROUNDID in $((SECONDS - START))s, identified after ${IDENTIFIED_AT}s"
