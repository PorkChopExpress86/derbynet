#! /bin/bash
#
# Races one scheduled round with the real derby-timer.jar and its simulated
# timer, the way timer/testing/fake-timer runs it, and fails unless every heat
# gets a result (#32).
#
# Usage: race-with-derby-timer.sh <base_url> [<derby-timer.jar>]
#
# Build the jar first with `ant -noinput -buildfile timer/build.xml`.  Needs
# java on the path, or the JAVA below.  Starts from an empty database.
#
# The jar's output goes to testing/derby-timer.log, and its own log, with a
# millisecond time on every line and every heartbeat traced (-th), to
# testing/timer-*.log.  The script prints a timeline of the round as it polls.
# A jar that has not identified its timer after DUMP_AFTER seconds gets a
# thread dump (SIGQUIT) in derby-timer.log, and a failure takes two more.
# Both logs are printed after a failure, and after a pass that took a dump
# (#399).  testing/race-with-derby-timer-cases.sh checks each bound.
#
# JAVA, a command (default java), runs the jar in place of java.
# extras/docker/ci/checks.sh's timer check sets it to
# extras/docker/ci/timer-java, which runs the jar in the timer image (#518).

BASE_URL=$1
TESTING=`dirname $0`
JAR=${2:-$TESTING/../timer/dist/lib/derby-timer.jar}
set -e -E -o pipefail
source $TESTING/common.sh

# The bounds come from the step's 30 group-3 jobs (both databases) in the 15
# server.yml runs from 2026-11-17 16:04 to 2026-11-18 02:43 UTC, the failed
# attempt left out: from the jar's start, the round took 88.3 to 95.7 s, for
# 17 heats at the simulated timer's 5 s a heat (-pace 1, then the 4 s race in
# SimulatedDevice.HeatRunner.run).  So the jar identified its timer within
# about 11 s of its start.
#
# In the one failure (run 44181424925, attempt 1), the jar logged in and sent
# HELLO within a second of its start, then sent nothing more, not even a
# heartbeat, for 151 s, until IDENTIFIED.  It then raced heats 1 to 6 at the
# usual 5 s each, and one deadline of 180 polls for the whole round ended the
# run 4 s after heat 7 started.
#
# IDENTIFY_SECONDS bounds the wait for the jar to identify its timer, from
# the jar's start: about twice that 151 s.
IDENTIFY_SECONDS=${IDENTIFY_SECONDS:-300}
# RACE_SECONDS bounds the whole round once the timer is identified: about
# twice the slowest 95.7 s.
RACE_SECONDS=${RACE_SECONDS:-180}
# HEAT_SECONDS bounds each heat: six times the 5 s a heat takes.
HEAT_SECONDS=${HEAT_SECONDS:-30}
# A thread dump after DUMP_AFTER seconds with the timer not identified yet,
# about three times the 11 s it took at most; the wait goes on.
DUMP_AFTER=${DUMP_AFTER:-30}
# The longest one poll may take, so that a hung poll counts as a failed one
# and the loop goes on.  10 s is far above the polls measured: in run
# 44181424925's attempt 1, the Django log has 182 polls from 21:40:52 to
# 21:43:59, 180 of them each after the old loop's 1 s sleep, so a poll took
# about 0.04 s on average (#399 retro).
POLL_SECONDS=10

# Django's timer states (TimerStatus.State, server/racing/models.py).
CONNECTED=2
STAGING=3
RUNNING=4

TIMER_LOG="$TESTING/derby-timer.log"
JAVA=${JAVA:-java}

user_login_coordinator
$TESTING/reset-database.sh "$BASE_URL"
$TESTING/import-roster.sh "$BASE_URL"
user_login_coordinator
curl_postj action.php "action=settings.write&n-lanes=4&unused-lane-mask=0" | check_jsuccess
curl_postj action.php "action=racer.bulk&who=all&what=checkin" | check_jsuccess
ROUNDID=$(curl_getj "action.php?query=poll.coordinator" | jq '.rounds[0].roundid')
curl_postj action.php "action=schedule.generate&roundid=$ROUNDID" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$ROUNDID&now_racing=1" | check_jsuccess

# poll.coordinator, bounded, recording nothing (as lookup_get records
# nothing): the polls are not part of any transcript.
poll() {
    curl --location -s --max-time $POLL_SECONDS -b $COOKIES_CURL \
         "$BASE_URL/action.php?query=poll.coordinator"
}

# Prints "<heats_scheduled> <heats_run> <timer state>" for the round, such as
# "17 6 4", or nothing when the poll fails.
progress() {
    poll | jq -r --argjson r "$ROUNDID" \
        '"\(.rounds[] | select(.roundid == $r) | "\(.heats_scheduled) \(.heats_run)") \(.["timer-state"].state)"' \
        2>/dev/null || true
}

read -r HEATS _ _ <<< "$(progress)"
[[ "$HEATS" =~ ^[1-9][0-9]*$ ]] || test_fails "round $ROUNDID has no heats scheduled: '$HEATS'"

# The jar's log and the script's timeline use the same local time.
stamp() {
    date +%H:%M:%S.%3N
}

rm -f "$TESTING"/timer-*.log
: > "$TIMER_LOG"
{ echo "$(stamp) uptime: $(uptime)"; "$JAVA" -version 2>&1; } >&2
"$JAVA" -jar "$JAR" -x -t -th -r -logdir "$TESTING" -simulate-timer -lanes 4 -pace 1 \
     "$BASE_URL" >> "$TIMER_LOG" 2>&1 &
JAR_PID=$!
START=$SECONDS

# True while the process this script started runs: its parent is this script
# and it is no zombie.  A pid reused after it ended has another parent.
jar_alive() {
    [ "$(awk '/^PPid:/ { print $2 }' /proc/$JAR_PID/status 2>/dev/null)" = "$$" ] \
        && ! grep -q '^State:.*zombie' /proc/$JAR_PID/status 2>/dev/null
}
# On the way out the jar gets SIGTERM, and the script waits for it to end:
# timer-java's `docker run` ends only once its container is gone, so the
# caller's cleanup finds no container half removed (#518 review).  Up to
# 10 s, 20 times the 0.5 s its container took to end and be removed after a
# pass of checks.sh timer on amd64 (#518).
stop_jar() {
    jar_alive || return 0
    echo "$(stamp) SIGTERM to pid $JAR_PID" >&2
    kill $JAR_PID 2>/dev/null || true
    for _ in $(seq 20); do
        jar_alive || { echo "$(stamp) pid $JAR_PID ended" >&2; return 0; }
        sleep 0.5  # 20 passes of 0.5 s make the 10 s above
    done
    echo "$(stamp) pid $JAR_PID still runs" >&2
}
trap stop_jar EXIT

# SIGQUIT makes the JVM print every thread's stack to its stdout, the jar's
# log, ending with a "JNI global refs" line.  Sent only to this script's own
# child, and only once it runs java, or docker, whose `docker run` in
# timer-java passes the signal on to the container's java: the running
# executable, not comm, which a script named java shares.
DUMPS=0
thread_dump() {
    local exe
    exe=$(basename "$(readlink /proc/$JAR_PID/exe 2>/dev/null)")
    if jar_alive && { [ "$exe" = java ] || [ "$exe" = docker ]; }; then
        echo "===== $(stamp) thread dump: $1" >> "$TIMER_LOG"
        local before
        before=$(grep -c '^JNI global refs' "$TIMER_LOG" || true)
        kill -QUIT $JAR_PID 2>/dev/null || true
        DUMPS=$((DUMPS + 1))
        # Wait for the dump's last line, up to 5 s.
        for _ in $(seq 10); do
            [ "$(grep -c '^JNI global refs' "$TIMER_LOG" || true)" -gt "$before" ] && break
            sleep 0.5
        done
    else
        echo "$(stamp) no thread dump: pid $JAR_PID is not this script's java or docker" >&2
    fi
}

print_logs() {
    echo "===== $TIMER_LOG"
    cat "$TIMER_LOG"
    # A jar that never started wrote no log of its own.
    for f in "$TESTING"/timer-*.log; do
        [ -e "$f" ] || continue
        echo "===== $f"
        cat "$f"
    done
}

fail_race() {
    thread_dump "$1"
    # Two dumps 2 s apart, four heartbeats at HttpTask's 500 ms pace, show
    # which threads move.
    sleep 2
    thread_dump "$1, again"
    print_logs
    # test_fails prints the last poll as RECEIVED.
    poll > "$DEBUG_CURL" || true
    test_fails "derby-timer.jar did not complete round $ROUNDID: $1"
}

# The timer counts as identified once Django has it connected, staging or
# running, the states IDENTIFIED and the heats leave it in
# (racing/services/timer.py), or once a heat has a result.
SHOWN=""
IDENTIFIED_AT=""
while true; do
    # Each pass sleeps 1 s and polls, so it takes 1 s plus the poll, up to
    # POLL_SECONDS more.  The bounds above compare NOW, the wall-clock
    # seconds since the jar started, so a long pass delays the next check,
    # not the time a bound measures.
    sleep 1
    NOW=$((SECONDS - START))
    jar_alive || fail_race "derby-timer.jar exited after ${NOW}s"
    # The bounds on identifying go by the polls before this one, so each
    # fires at its time whatever this poll shows: a timer first seen
    # identified at or past a bound fails, or gets its dump.  Checked after
    # the poll, a jar already staging at the first poll passed
    # IDENTIFY_SECONDS=1 and took no dump at DUMP_AFTER=0 (#399).
    if [ -z "$IDENTIFIED_AT" ]; then
        if [ $NOW -ge $IDENTIFY_SECONDS ]; then
            fail_race "the timer was not identified within ${IDENTIFY_SECONDS}s"
        elif [ $NOW -ge $DUMP_AFTER ] && [ $DUMPS = 0 ]; then
            thread_dump "timer not identified after ${NOW}s"
            # The dump waits up to 5 s for its last line.
            NOW=$((SECONDS - START))
        fi
    fi
    read -r _ RUN STATE <<< "$(progress)"
    if ! [[ "$RUN" =~ ^[0-9]+$ ]]; then
        echo "$(stamp) +${NOW}s the poll failed" >&2
        RUN=-1
        STATE=
    elif [ "$RUN $STATE" != "$SHOWN" ]; then
        echo "$(stamp) +${NOW}s heats_run=$RUN of $HEATS, timer state $STATE" >&2
        SHOWN="$RUN $STATE"
    fi
    if [ -z "$IDENTIFIED_AT" ]; then
        if [ "$RUN" -gt 0 ] || [ "$STATE" = $CONNECTED ] \
               || [ "$STATE" = $STAGING ] || [ "$STATE" = $RUNNING ]; then
            IDENTIFIED_AT=$NOW
            IDENTIFIED_BY="heats_run=$RUN of $HEATS, timer state $STATE"
            HEAT_START=$NOW
            HEATS_DONE=$RUN
        fi
        continue
    fi
    [ "$RUN" -ge "$HEATS" ] && break
    if [ "$RUN" -gt "$HEATS_DONE" ]; then
        HEATS_DONE=$RUN
        HEAT_START=$NOW
    elif [ $((NOW - HEAT_START)) -ge $HEAT_SECONDS ]; then
        fail_race "no heat finished within ${HEAT_SECONDS}s of the last, after $HEATS_DONE"
    fi
    if [ $((NOW - IDENTIFIED_AT)) -ge $RACE_SECONDS ]; then
        fail_race "the round did not finish within ${RACE_SECONDS}s of identifying the timer"
    fi
done

# A pass that took a dump prints what it caught.
[ $DUMPS = 0 ] || print_logs
echo "derby-timer.jar raced every heat of round $ROUNDID in $((SECONDS - START))s, identified after ${IDENTIFIED_AT}s at $IDENTIFIED_BY"
