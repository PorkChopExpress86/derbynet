#! /bin/bash

# With DERBYNET_BACKEND=django, the checks expect what the Django server does
# where it differs from PHP on purpose: fitting a racer in never changes the
# running heat or the staged heat (D34, quirk Q20), and a racer who is not on
# the round's roster is refused with notonroster (D39, quirk Q25).  Unset, the
# script checks PHP as it always has.


BASE_URL=$1

STRESS4LANES=${2:-5}
STRESS6LANES=${3:-$STRESS4LANES}

set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=reschedule "`dirname $0`/reset-database.sh" "$BASE_URL"
"`dirname $0`/import-roster.sh" "$BASE_URL"


## TODO -
##
## Run one heat of four, with four racers on four lanes.
## Add a late entrant and adjust schedule: five heats.
## Edit the late racer to be in a different group.  Adjust the schedule: five heats with byes.
## Edit the late racer to be back in the group.  Adjust the schedule: seems to be a bug with the last heat.
##
## Add a test for this.

# 4 lanes, all in use
curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess

# Racer ids for roundid 5 are 5, 10, 15, 20, 25, ..., 80
# Car numbers are 405, 410, ..., 480

# Check in 4 racers (5, 15, 25, 35)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 405)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 415)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 425)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 435)&value=1" | check_jsuccess

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Arrows 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Arrows 1)&now_racing=1" | check_jsuccess
run_heat $(roundid_of Arrows 1)  1  405:4.050 435:4.350 425:4.250 415:4.150
# 111 101 131 121
# 121 111 101 131
# 131 121 111 101

# Add latecomer 80 (car 480)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 480)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Arrows 1)&trace=1" | check_jsuccess
if [ "$DERBYNET_BACKEND" = django ] ; then
# Heat 2 is running and heat 3 is staged, so Django keeps both and fits car
# 480 into heat 4 and added heats (D34, Q20).
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        == [null,
            "415 405 435 425",
            "425 415 405 435",
            "480 425 415 405",
            "435 480 - -",
            "- - 480 -",
            "- - - 480"]' testing/debug.curl > /dev/null || test_fails
else
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        | .[1] == "480 405 435 425" and
                .[2] == "425 415 480 435" and
                .[3] == "435 480 415 405" and
                .[4] == "415 425 405 480"' testing/debug.curl > /dev/null || test_fails
fi

# Oops, it wasn't actually latecomer 80
curl_postj action.php "action=racer.pass&racer=$(racerid_of 480)&value=0" | check_jsuccess
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Arrows 1)&trace=1" | check_jsuccess
# So now there are byes in the schedule
if [ "$DERBYNET_BACKEND" = django ] ; then
# Car 480's lanes go empty, and the added heats stay, empty (D34, Q20).
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        == [null,
            "415 405 435 425",
            "425 415 405 435",
            "- 425 415 405",
            "435 - - -",
            "- - - -",
            "- - - -"]' testing/debug.curl > /dev/null || test_fails
else
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        | .[1] == "- 405 435 425" and
                .[2] == "425 415 - 435" and
                .[3] == "435 - 415 405" and
                .[4] == "415 425 405 -"' testing/debug.curl > /dev/null || test_fails
fi

# Oh, latecomer 80 really HAS arrived
curl_postj action.php "action=racer.pass&racer=$(racerid_of 480)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Arrows 1)&trace=1" | check_jsuccess

# So put them back in the schedule
if [ "$DERBYNET_BACKEND" = django ] ; then
# Heats 2 and 3 stay as they are again, and car 480 goes into heats 4 to 7
# (D34, Q20).
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        == [null,
            "415 405 435 425",
            "425 415 405 435",
            "- 480 415 405",
            "480 - - -",
            "435 - 480 -",
            "- 425 - 480"]' testing/debug.curl > /dev/null || test_fails
else
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        | .[1] == "480 405 435 425" and
                .[2] == "425 415 480 435" and
                .[3] == "435 480 415 405" and
                .[4] == "415 425 405 480"' testing/debug.curl > /dev/null || test_fails
fi

RESET_SOURCE=reschedule-2 "`dirname $0`/reset-database.sh" "$BASE_URL"
"`dirname $0`/import-roster.sh" "$BASE_URL"

# 4 lanes, 1 masked, 3 in use.
curl_postj action.php "action=settings.write&unused-lane-mask=2&n-lanes=4" | check_jsuccess

# curl_getj "action.php?query=poll&values=racers,rounds"
# curl_getj "action.php?query=racer.list"

# Racer ids for roundid 1 are 1, 6, 11, 16, 21, 26, ..., 81

# Check in 2 racers for Lions & Tigers (roundid 1)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 101)&value=1" | check_jsuccess
# curl_postj action.php "action=racer.pass&racer=6&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 111)&value=1" | check_jsuccess

## Test case: 2 racers checked in, 4 lane track with 1 masked lane.  First heat run, add latecomer.
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# heat 1: 101 -  -  111
# heat 2: 111 - 101  -
# heat 3:  -  - 111 101

run_heat $(roundid_of Lions 1)  1  101:1.010 - - 111:1.110

curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 2 racers 4 lanes 1 masked 1 heat run add racer 6
jq .chart testing/debug.curl

## Test case: 3 racers checked in, 4 lane track with 1 masked lane.  First heat run, add latecomer.
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 116)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# 101 - 116 111
# 111 - 101 116
# 116 - 111 101
run_heat $(roundid_of Lions 1)  1  101:1.010 - 116:1.160 111:1.110

curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 3 racers 4 lanes 1 masked 1 heat run add racer 6
jq .chart testing/debug.curl

## Test case: 4 racers checked in, generate schedule, race 1 heat, add latecomer
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# 101 - 121 116
# 111 - 101 121
# 116 - 111 101
# 121 - 116 111
run_heat $(roundid_of Lions 1)  1  101:1.010 - 121:1.210 116:1.160
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 1 heat run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  2  111:1.110 - 101:1.010 121:1.210
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 2 heats run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  3  116:1.160 - 111:1.110 101:1.010
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 3 heats run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  4  121:1.210 - 116:1.160 111:1.110 x
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 4 heats run add racer 6
jq .chart testing/debug.curl


## Reset
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=0" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 116)&value=0" | check_jsuccess

############## 2 runs per lane
echo ============= 2 runs per lane ======================

curl_postj action.php "action=schedule.generate&n_times_per_lane=2&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# heat 1: 101 -  -  111
# heat 2: 111 - 101  -
# heat 3:  -  - 111 101

run_heat $(roundid_of Lions 1)  1  101:1.010 - - 111:1.110

curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 2 racers 4 lanes 1 masked 1 heat run add racer 6
 jq .chart testing/debug.curl

## Test case: 3 racers checked in, 4 lane track with 1 masked lane.  First heat run, add latecomer.
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 116)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&n_times_per_lane=2&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# 101 - 116 111
# 111 - 101 116
# 116 - 111 101
run_heat $(roundid_of Lions 1)  1  101:1.010 - 116:1.160 111:1.110

curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 3 racers 4 lanes 1 masked 1 heat run add racer 6
jq .chart testing/debug.curl

## Test case: 4 racers checked in, generate schedule, race 1 heat, add latecomer
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&n_times_per_lane=2&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess
# 101 - 121 116
# 111 - 101 121
# 116 - 111 101
# 121 - 116 111
run_heat $(roundid_of Lions 1)  1  101:1.010 - 121:1.210 116:1.160
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 1 heat run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  2  111:1.110 - 101:1.010 121:1.210
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 2 heats run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  3  116:1.160 - 111:1.110 101:1.010
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 3 heats run add racer 6
jq .chart testing/debug.curl

run_heat $(roundid_of Lions 1)  4  121:1.210 - 116:1.160 111:1.110 x
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of Lions 1)&racerid=$(racerid_of 106)&dry-run=1&trace=1" | check_jsuccess
echo 4 racers 4 lanes 1 masked 4 heats run add racer 6
jq .chart testing/debug.curl

echo ============================= Movement tests ==============================

## Test case: All racers for roundid 1 and 2 checked in, generate schedules,
##   race one (arbitrary) heat in each, then move a raced racer from roundid 1 to roundid 2.
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=racer.bulk&what=checkin&who=c$(classid_of Lions)&value=1" | check_jsuccess
curl_postj action.php "action=racer.bulk&what=checkin&who=c$(classid_of White)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |  .heats_scheduled == 17" >/dev/null || test_fails
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |  .heats_scheduled == 17" >/dev/null || test_fails

curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=4" | check_jsuccess
# Racer ids are 41, 51, 66
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess
run_heat $(roundid_of Lions 1)  4  141:1.210 - 151:1.160 166:1.110 x

curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&heat=6" | check_jsuccess
# Racer ids are 82, 7, 22
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess
run_heat $(roundid_of White 1)  6  282:1.234 - 207:1.516 222:1.611 x

curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) '.["current-heat"] | .roundid == $white_r1 and .heat == 7' \
       >/dev/null || test_fails

echo ============= Move racer 61 from roundid 1 to roundid 2 ======================
curl_postj action.php "action=racer.edit&racerid=$(racerid_of 161)&partitionid=$(partitionid_of White)" | check_jsuccess
# That leaves racer 61 departed from roundid 1 and unscheduled in roundid 2
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"departed"'"'")" \
       >/dev/null || test_fails
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails

curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 16 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails


# Having added a new racer to roundid 2, rescheduling adds a new heat.
curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of White 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 16 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments | length) == 0" \
       >/dev/null || test_fails

echo ============= Move racer 62 from roundid 2 to roundid 1 ======================
curl_postj action.php "action=racer.edit&racerid=$(racerid_of 262)&partitionid=$(partitionid_of Lions)" | check_jsuccess
# Merely moving the racer doesn't change the schedules
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 16 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"departed"'"'")" \
       >/dev/null || test_fails

curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"departed"'"'")" \
       >/dev/null || test_fails

curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of White 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
if [ "$DERBYNET_BACKEND" = django ] ; then
# Django leaves car 262's lanes empty and squeezes out no heat, so the round
# keeps its 18 heats and racing stays on (D34, Q20).  Only a committed
# reschedule that squeezes out a heat turns racing off
# (server/racing/services/reschedule.py).
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments | length) == 0" \
       >/dev/null || test_fails

curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) '.["current-heat"] | .roundid == $white_r1 and .heat == 7 and .now_racing == true' \
       >/dev/null || test_fails
else
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 17 and (.adjustments | length) == 0" \
       >/dev/null || test_fails

curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) '.["current-heat"] | .roundid == $white_r1 and .heat == 7 and .now_racing == false' \
       >/dev/null || test_fails
fi

# The last raced heat at this point is heat 5, rather than heat 6,
# so the correct next heat would be heat 6.

echo ============= Move already-raced racer 41 from roundid 1 to roundid 2 ======================
curl_postj action.php "action=racer.edit&racerid=$(racerid_of 141)&partitionid=$(partitionid_of White)" | check_jsuccess
# Merely moving the racer doesn't change the schedules
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"departed"'"'")" \
       >/dev/null || test_fails
if [ "$DERBYNET_BACKEND" = django ] ; then
# Still the 18 heats Django kept above.
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails
else
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails
fi

curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
if [ "$DERBYNET_BACKEND" = django ] ; then
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails
else
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 17 and (.adjustments |
                  length == 1 and .[0] .why == "'"'"unscheduled"'"'")" \
       >/dev/null || test_fails
fi

curl_postj action.php "action=schedule.reschedule&trace=1&roundid=$(roundid_of White 1)" | check_jsuccess
if [ "$DERBYNET_BACKEND" = django ] ; then
# The chart the White heats below are raced from (Q20).  run_heat skips empty
# lanes, so this is what catches a car in a lane that should be empty.
jq -e --argjson cars "$(racer_carnumbers)" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end) | join(" ") else . end)
        == ["141 - 212 227",
            "207 - 141 232",
            "237 - 161 141",
            "242 - 252 161",
            "247 - 257 272",
            null,
            "252 - - 277",
            "212 - 222 237",
            "217 - 227 242",
            "257 - 267 282",
            "- - 272 202",
            "222 - 232 247",
            "267 - 277 207",
            "227 - 237 252",
            "232 - 242 257",
            "272 - 282 212",
            "277 - 202 217",
            "202 - 247 267",
            "161 - 217 -"]' testing/debug.curl > /dev/null || test_fails
fi
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson lions_r1 $(roundid_of Lions 1) ".rounds | map(select(.roundid == \$lions_r1))[0] |
           .heats_scheduled == 17 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
if [ "$DERBYNET_BACKEND" = django ] ; then
# Heat 7 is running and heat 8 staged, so Django fits car 141 into heats 1
# to 3 and adds a heat 19 (D34, Q20).
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 19 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
else
curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson white_r1 $(roundid_of White 1) ".rounds | map(select(.roundid == \$white_r1))[0] |
           .heats_scheduled == 18 and (.adjustments | length) == 0" \
       >/dev/null || test_fails
fi

curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&heat=1" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

if [ "$DERBYNET_BACKEND" = django ] ; then
# Django's 19 heats, from the chart of the reschedule above.  Heat 6 ran
# before the moves and keeps its number (D34), so racing stops after heat 5
# and goes on from heat 7.  In these heats each car gets the times it gets
# in PHP's heats after the moves, so the standings below are the same.
ROUND=$(roundid_of White 1)
run_heat $ROUND 1  141:2.685 - 212:2.522 227:3.274
run_heat $ROUND 2  207:1.724 - 141:3.484 232:1.239
run_heat $ROUND 3  237:2.203 - 161:2.066 141:1.791
run_heat $ROUND 4  242:3.397 - 252:3.462 161:1.462
run_heat $ROUND 5  247:1.374 - 257:1.455 272:3.399 x

curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&heat=7" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

ROUND=$(roundid_of White 1)
run_heat $ROUND 7  252:1.033 - -         277:3.656
run_heat $ROUND 8  212:1.905 - 222:1.269 237:2.036
run_heat $ROUND 9  217:1.037 - 227:3.209 242:3.259
run_heat $ROUND 10 257:2.728 - 267:2.500 282:1.043
run_heat $ROUND 11 -         - 272:1.116 202:3.460
run_heat $ROUND 12 222:3.141 - 232:2.654 247:3.970
run_heat $ROUND 13 267:2.015 - 277:2.638 207:3.607
run_heat $ROUND 14 227:2.474 - 237:3.020 252:2.505
run_heat $ROUND 15 232:3.771 - 242:1.665 257:1.105
run_heat $ROUND 16 272:2.702 - 282:3.953 212:2.385
run_heat $ROUND 17 277:1.756 - 202:2.212 217:3.306
run_heat $ROUND 18 202:3.423 - 247:1.878 267:3.701
run_heat $ROUND 19 161:3.028 - 217:1.825 -         x
else
ROUND=$(roundid_of White 1)
run_heat $ROUND 1  141:2.685 - 217:1.037  232:1.239
run_heat $ROUND 2  237:2.203 - 161:2.066  141:3.484
run_heat $ROUND 3  242:3.397 - 252:3.462  161:1.462
run_heat $ROUND 4  247:1.374 - 141:1.791  272:3.399 x

curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&heat=6" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

ROUND=$(roundid_of White 1)
run_heat $ROUND 6  252:1.033 - 212:2.522  277:3.656
run_heat $ROUND 7  212:1.905 - 222:1.269  237:2.036
run_heat $ROUND 8  217:3.306 - 227:3.274  242:3.259
run_heat $ROUND 9  257:1.455 - 267:2.500  282:1.043
run_heat $ROUND 10 161:3.028 - 272:1.116  202:3.460
run_heat $ROUND 11 222:3.141 - 232:2.654  247:3.970
run_heat $ROUND 12 267:2.015 - 277:2.638  207:1.724
run_heat $ROUND 13 227:3.209 - 237:3.020  252:2.505
run_heat $ROUND 14 232:3.771 - 242:1.665  257:2.728
run_heat $ROUND 15 272:2.702 - 282:3.953  212:2.385
run_heat $ROUND 16 277:1.756 - 202:2.212  217:1.825
run_heat $ROUND 17 202:3.423 - 247:1.878  267:3.701
run_heat $ROUND 18 207:3.607 - 257:1.105  227:2.474 x
fi

curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=1" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

ROUND=$(roundid_of Lions 1)
run_heat $ROUND 1  262:1.220 - 111:3.969 126:2.213
run_heat $ROUND 2  136:1.996 - 262:3.471 131:2.107 x

curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=4" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

ROUND=$(roundid_of Lions 1)
run_heat $ROUND 4  146:1.605 - 156:3.410 171:1.066
run_heat $ROUND 5  181:1.775 - 106:2.603 121:1.963
run_heat $ROUND 6  151:2.006 - 116:2.242 176:3.638
run_heat $ROUND 7  111:2.940 - 121:3.589 136:2.095
run_heat $ROUND 8  116:2.154 - 126:3.967 -
run_heat $ROUND 9  156:3.899 - 166:3.795 181:3.820
run_heat $ROUND 10 106:1.681 - 171:2.746 101:3.013
run_heat $ROUND 11 121:1.838 - 131:2.441 146:1.674
run_heat $ROUND 12 166:2.077 - 176:2.199 106:1.620
run_heat $ROUND 13 126:2.674 - 136:3.644 151:2.212
run_heat $ROUND 14 131:2.028 - -         156:2.573
run_heat $ROUND 15 171:3.340 - 181:1.379 111:2.618
run_heat $ROUND 16 176:1.963 - 101:2.289 262:2.197
run_heat $ROUND 17 101:2.581 - 146:1.692 116:3.143 x

# Car 141's lone heat in the first round of Lions & Tigers would make it fastest
# in the round if it were counted in standings.  This is to confirm it's not.
# A round's standings are under the key "st-r" and the round's ID.
if [ "$DERBYNET_BACKEND" = django ] ; then
# Django does not serve query=standings, so read the same places from the
# standings sheets export-json.php serves, one per den; each den races only
# its first round here.
expect_places "Lions & Tigers" Place 146 1 151 2 106 3
expect_places "White's Wolves" Place 257 1 222 2 217 3
[ -z "$(standings_row "Lions & Tigers" 141)" ] || \
    test_fails Car 141 should have no row in the Lions standings
else
curl_getj "action.php?query=standings" | \
    jq -e --arg lions "st-r$(roundid_of Lions 1)" --arg white "st-r$(roundid_of White 1)" \
          --argjson car146 $(racerid_of 146) --argjson car151 $(racerid_of 151) \
          --argjson car106 $(racerid_of 106) --argjson car257 $(racerid_of 257) \
          --argjson car222 $(racerid_of 222) --argjson car217 $(racerid_of 217) \
          '.standings | .[$lions][0][0] == $car146 and
                        .[$lions][1][0] == $car151 and
                        .[$lions][2][0] == $car106 and
                        .[$white][0][0] == $car257 and
                        .[$white][1][0] == $car222 and
                        .[$white][2][0] == $car217' \
        >/dev/null || test_fails
fi

echo ============= 4 lanes 17 racers roundid 2 ======================

curl_postj action.php "action=database.purge&purge=schedules&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=database.purge&purge=schedules&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess
curl_postj action.php "action=racer.bulk&what=checkin&who=c$(classid_of White)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&now_racing=1" | check_jsuccess

for i in {1..17}
do
    run_heat $(roundid_of White 1)  $i  1.0 2.0 3.0 4.0 x

    echo 17 racers 4 lanes $i heats run add racer 6
    if [ "$DERBYNET_BACKEND" = django ] ; then
    # Car 106 is a Lion, not on this round's roster (D39, Q25).
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$(racerid_of 106)&dry-run=1" | check_jfailure
    jq -e '.outcome.code == "notonroster"' testing/debug.curl >/dev/null || test_fails
    else
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$(racerid_of 106)&dry-run=1" | check_jsuccess
    fi
    jq .chart testing/debug.curl
done

# Full coverage would be more like this:
# Loop over number of lanes
#   Loop over lane mask(s)
#     Loop over number of racers checked in for the round (1..nlanes and then high values)
#       Loop over number of heats to run before latecomer
#         Loop over number of latecomers to add
#
# Would take forever to run, though, and would generate a huge number of cases
# to check by hand.

############## Adding racers repeatedly: 4 lanes, 17 racers (roundid 2)
echo ============= Adding racers repeatedly: 4 lanes 17 racers $STRESS4LANES latecomers roundid 2 ======================

curl_postj action.php "action=database.purge&purge=schedules&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&now_racing=1" | check_jsuccess
run_heat $(roundid_of White 1)  1  1.0 2.0 3.0 4.0 x

if [ "$DERBYNET_BACKEND" = django ] ; then
# PHP fits in a latecomer that partition=2 puts in a new partition, but Django
# refuses one who is not on the round's roster (D39, Q25).  So on Django the
# latecomers below join White's Wolves instead, and the 6-lane ones get car
# numbers 910 on, as the 4-lane ones stay on White's roster.
curl_postj action.php "action=racer.import&firstname=F-other&lastname=L-other&carnumber=999&partition=2" | check_jsuccess
curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$(jq .racerid testing/debug.curl)" | check_jfailure
jq -e '.outcome.code == "notonroster"' testing/debug.curl >/dev/null || test_fails

# late_chart_is <lanes> <carnumber>: the chart of the reschedule just made,
# by car number, with the latecomer's car as L, is the one Django gives for a
# latecomer in the stress loop on <lanes> lanes.  None of the latecomers is
# checked in, so each reschedule drops the one before, and every latecomer
# gets the same lanes.  On 4 lanes this is the chart of Q20's exchanges 039 to
# 044, the first six latecomers; on 6 lanes, a real Django run's, the same for
# all six latecomers of the default run.
late_chart_is() {
    local WANT
    case $1 in
        4) WANT='[null,
                  "207 217 232 161",
                  "212 222 237 267",
                  "217 227 141 272",
                  "222 232 242 277",
                  "227 237 247 282",
                  "232 141 252 L",
                  "237 L 257 207",
                  "L 247 161 212",
                  "242 252 267 217",
                  "247 257 272 222",
                  "252 161 277 227",
                  "257 267 282 232",
                  "161 272 202 237",
                  "267 277 207 141",
                  "272 282 L 242",
                  "277 202 217 247",
                  "282 207 222 252",
                  "141 242 212 202"]' ;;
        6) WANT='[null,
                  "232 141 247 161 202 242",
                  "257 267 277 207 232 272",
                  "207 L 227 141 161 222",
                  "L 242 252 267 207 247",
                  "161 272 282 L 237 277",
                  "212 222 232 242 L 227",
                  "141 247 257 272 212 252",
                  "267 277 L 217 141 282",
                  "217 227 237 247 272 232",
                  "242 252 161 277 217 257",
                  "272 282 207 222 242 202",
                  "222 232 141 252 277 237",
                  "247 257 267 282 222 161",
                  "277 202 212 227 247 207",
                  "227 237 242 257 282 141",
                  "252 161 272 202 227 267",
                  "282 207 217 232 252 212",
                  "237 217 202 212 267 L"]' ;;
    esac
    jq -e --argjson cars "$(racer_carnumbers)" --arg late "$2" --argjson want "$WANT" '.chart
        | map(if type == "string" then split(" ") | map(if . == "-" then . else ($cars[.] | tostring) end)
                                                  | map(if . == $late then "L" else . end) | join(" ") else . end)
        == $want' testing/debug.curl >/dev/null || \
        test_fails Chart for latecomer car $2 on $1 lanes
}
fi

for i in $( seq 0 $STRESS4LANES )
do
    if [ "$DERBYNET_BACKEND" = django ] ; then
    CARNO=90$i
    curl_postj action.php "action=racer.import&firstname=F4-late$i&lastname=L4-late$i&carnumber=$CARNO&partition=White%27s%20Wolves" | check_jsuccess
    else
    curl_postj action.php "action=racer.import&firstname=F4-late$i&lastname=L4-late$i&carnumber=90$i&partition=2" | check_jsuccess
    fi
    RACERID=$(jq .racerid testing/debug.curl)

    START=$(date +%s)
    if [ "$DERBYNET_BACKEND" = django ] ; then
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$RACERID" | check_jsuccess
    late_chart_is 4 $CARNO
    else
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$RACERID" | check_jsuccess
    fi
    END=$(date +%s)

    jq .chart testing/debug.curl
    jq ".chart | length" testing/debug.curl
    echo "Elapsed time $(($END-$START)) second(s)"
    jq .times testing/debug.curl
done

############## Adding racers repeatedly: 6 lanes, 17 racers (roundid 2)
echo ============= Adding racers repeatedly: 6 lanes 17 racers $STRESS6LANES latecomers roundid 2 ======================

curl_postj action.php "action=database.purge&purge=schedules&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=6" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&now_racing=1" | check_jsuccess
run_heat $(roundid_of White 1)  1  1.0 2.0 3.0 4.0 5.0 6.0 x


for i in $( seq 0 $STRESS6LANES )
do
    if [ "$DERBYNET_BACKEND" = django ] ; then
    CARNO=91$i
    curl_postj action.php "action=racer.import&firstname=F6-late$i&lastname=L6-late$i&carnumber=$CARNO&partition=White%27s%20Wolves" | check_jsuccess
    else
    curl_postj action.php "action=racer.import&firstname=F6-late$i&lastname=L6-late$i&carnumber=90$i&partition=2" | check_jsuccess
    fi
    RACERID=$(jq .racerid testing/debug.curl)

    START=$(date +%s)
    if [ "$DERBYNET_BACKEND" = django ] ; then
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$RACERID" | check_jsuccess
    late_chart_is 6 $CARNO
    else
    curl_postj action.php "action=schedule.reschedule&roundid=$(roundid_of White 1)&racerid=$RACERID" | check_jsuccess
    fi
    END=$(date +%s)

    jq .chart testing/debug.curl
    jq ".chart | length" testing/debug.curl
    echo "Elapsed time $(($END-$START)) second(s)"
    jq .times testing/debug.curl
done

exit
