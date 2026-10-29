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

BASE_URL=$1
JAR=${2:-`dirname $0`/../timer/dist/lib/derby-timer.jar}
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator
`dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"
user_login_coordinator
curl_postj action.php "action=settings.write&n-lanes=4&unused-lane-mask=0" | check_jsuccess
curl_postj action.php "action=racer.bulk&who=all&what=checkin" | check_jsuccess
ROUNDID=$(curl_getj "action.php?query=poll.coordinator" | jq '.rounds[0].roundid')
curl_postj action.php "action=schedule.generate&roundid=$ROUNDID" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$ROUNDID&now_racing=1" | check_jsuccess

java -jar "$JAR" -x -t -r -simulate-timer -lanes 4 -pace 1 "$BASE_URL" \
     > "`dirname $0`/derby-timer.log" 2>&1 &
JAR_PID=$!
trap 'kill $JAR_PID 2>/dev/null || true' EXIT

# 180 seconds is ample for 17 heats at a 1 s pace.
for _ in $(seq 180); do
    sleep 1
    DONE=$(NOANNOUNCE=1 curl_getj "action.php?query=poll.coordinator" \
               | jq --argjson r "$ROUNDID" \
                    '.rounds[] | select(.roundid == $r) | .heats_scheduled > 0 and .heats_run == .heats_scheduled')
    [ "$DONE" = "true" ] && break
done

curl_getj "action.php?query=poll.coordinator" | tee /dev/stderr \
    | jq -e --argjson r "$ROUNDID" \
         '.rounds[] | select(.roundid == $r) | .heats_scheduled > 0 and .heats_run == .heats_scheduled' \
         >/dev/null || {
    cat "`dirname $0`/derby-timer.log"
    test_fails "derby-timer.jar did not complete round $ROUNDID"
}
echo "derby-timer.jar raced every heat of round $ROUNDID"
