#! /bin/bash



BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=rotation-schedule "`dirname $0`/reset-database.sh" "$BASE_URL"
"`dirname $0`/import-roster.sh" "$BASE_URL"

curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=6&rotation-schedule=1" | check_jsuccess

# Check in just two cars to start
curl_postj action.php "action=racer.pass&racer=$(racerid_of 101)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 106)&value=1" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess


TMP=$(mktemp)

jq >$TMP <<EOF
{"schedule": [
[101, 106, null, null, null, null],
[106, null, null, null, null, 101],
[null, null, null, null, 101, 106],
[null, null, null, 101, 106, null],
[null, null, 101, 106, null, null],
[null, 101, 106, null, null, null]
    ]
}
EOF

# The schedule names racers by ID.  Compare car numbers.
( curl_getj "action.php?query=poll&values=schedule" | \
      jq --argjson cars "$(racer_carnumbers)" \
         '.schedule |= map(map(if . == null then null else $cars[tostring] end))' | diff $TMP - ) || test_fails

curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of Lions 1)" | check_jsuccess
# Now some more racers
curl_postj action.php "action=racer.pass&racer=$(racerid_of 111)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 116)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 126)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 131)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 136)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 141)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 146)&value=1" | check_jsuccess

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess

jq >$TMP <<EOF
{"schedule":[
[101, 106, 111, 116, 121, 126],
[106, 111, 116, 121, 126, 131],
[111, 116, 121, 126, 131, 136],
[116, 121, 126, 131, 136, 141],
[121, 126, 131, 136, 141, 146],
[126, 131, 136, 141, 146, 101],
[131, 136, 141, 146, 101, 106],
[136, 141, 146, 101, 106, 111],
[141, 146, 101, 106, 111, 116],
[146, 101, 106, 111, 116, 121]
    ]
}
EOF

curl_getj "action.php?query=poll&values=schedule" | \
    jq --argjson cars "$(racer_carnumbers)" \
       '.schedule |= map(map(if . == null then null else $cars[tostring] end))' | diff $TMP - || test_fails


rm $TMP
