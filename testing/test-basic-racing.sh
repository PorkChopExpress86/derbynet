#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess

### Check in every other racer...
`dirname $0`/test-basic-checkins.sh "$BASE_URL"

curl_postj action.php "action=class.edit&classid=$(classid_of White)&name=White's%20Wolves&ntrophies=4" | check_jsuccess

### Schedule first round for 3 of the classes
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Bears 1)" | check_jsuccess

# Can't delete a racer who's in a schedule
curl_postj action.php "action=racer.delete&racer=$(racerid_of 121)" | check_jfailure

# This is testing replay connected before we start racing
## curl_postj action.php "action=replay.message" | jq -e ".replay == [\"HELLO\"]" \
##        >/dev/null || test_fails

# The server treats a replay as connected for 10 seconds after its last message,
# and test-each-role.sh sent the last one some time ago.  Connect again, so that
# the replay messages below don't depend on how long the tests in between took.
curl_postj action.php "action=replay.message&status=-1&finished-replay=0" | check_jsuccess

### Racing for the first round of Lions & Tigers: 5 heats
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess

user_login_timer
curl_post action.php "action=timer-message&message=HELLO" | check_success
curl_post action.php "action=timer-message&message=IDENTIFIED&nlanes=4" | check_success

# TODO test replay connects after "now racing" started.

run_heat $(roundid_of Lions 1) 1 101:3.3294 121:3.4179 141:3.8182 111:2.2401

# The replay station logs in as a role that holds CONTROL_RACE (owner decision
# D16), so poll as the coordinator, not as the timer run_heat left us as.
user_login_coordinator
curl_postj action.php "action=replay.message" | \
    jq -e '.replay == ["CANCEL", "START Lions & Tigers_Round1_Heat01",
                       "RACE_STARTS 4000 2 50",
                       "REPLAY 4000 2 50",
                       "CANCEL",
                       "START Lions & Tigers_Round1_Heat02"]' \
       >/dev/null || test_fails
user_login_timer

ROUND=$(roundid_of Lions 1)
run_heat $ROUND 2 111:3.7554 131:2.6205 101:2.3800 121:3.2347
run_heat $ROUND 3 121:2.0793 141:3.6770 111:2.9511 131:2.8799
run_heat $ROUND 4 131:3.7412 101:3.4053 121:3.3414 141:2.8045
run_heat $ROUND 5 141:2.9661 111:3.9673 131:3.5686 101:3.8388    x

### Racing for the first round of White's Wolves: 5 heats
user_login_coordinator
curl_postj action.php "action=heat.select&roundid=$(roundid_of White 1)&now_racing=1" | check_jsuccess

user_login_timer
curl_post action.php "action=timer-message&message=HEARTBEAT" | check_success
cat $DEBUG_CURL | expect_one "<heat-ready[ />]"

ROUND=$(roundid_of White 1)
run_heat $ROUND 1  207:2.6149 227:2.0731 247:3.0402 217:3.7937
run_heat $ROUND 2  217:2.9945 237:3.4571 207:2.1867 227:2.3447

user_login_coordinator
curl_getj "action.php?query=poll.coordinator" | jq '.["last-heat"] == "available"' | expect_eq true
curl_postj action.php "action=heat.rerun&heat=last" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq '.["last-heat"] == "recoverable" and 
        (.["heat-results"] | all(has("finishtime") and has("finishplace")))' | \
    expect_eq true

curl_postj action.php "action=heat.reinstate" | grep last-heat | expect_one none
curl_getj "action.php?query=poll.coordinator" | \
    jq '.racers |
        all((.finishtime == 2.994 and (.name | test("Darrell.*"))) or 
            (.finishtime == 3.457 and .name == "Ian Ives") or
            (.finishtime == 2.187 and .name == "Blake Burling") or
            (.finishtime == 2.345 and .name == "Elliot Eastman"))' | \
    expect_eq true

curl_postj action.php "action=heat.select&heat=next&now_racing=1" | check_jsuccess
user_login_timer

ROUND=$(roundid_of White 1)
run_heat $ROUND 3  227:2.4901 247:2.0838 217:3.6469 237:2.1003
run_heat $ROUND 4  237:3.9403 207:3.4869 227:3.5717 247:3.5386
run_heat $ROUND 5  247:3.0439 217:3.4090 237:3.3881 207:2.9110  x

user_login_coordinator
### Un-checkin a few Bears & Freres racers in round 1 and re-generate schedule
curl_postj action.php "action=racer.pass&racer=$(racerid_of 313)&value=0" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 323)&value=0" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 333)&value=0" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Bears 1)" | check_jsuccess

curl_postj action.php "action=heat.select&roundid=$(roundid_of Bears 1)&now_racing=1" | check_jsuccess

user_login_timer
curl_post action.php "action=timer-message&message=HEARTBEAT" | check_success

### Racing for the first round of Bears & Freres: 4 heats among 3 racers

ROUND=$(roundid_of Bears 1)
run_heat $ROUND 1  303:2.7706 -          343:2.4508 139:2.4413
run_heat $ROUND 2  139:2.7384 303:3.0321 -          343:9.9999
run_heat $ROUND 3  343:9.9999 139:3.4490 303:2.8584 -
run_heat $ROUND 4  -          343:9.9999 139:2.7565 303:3.0264  x

user_login_coordinator
### Editing racers

EDITED_RACERID=$(racerid_of 405)
curl_getj "action.php?query=racer.list" | \
    jq -e --argjson id $EDITED_RACERID '.racers | map(select(.racerid == $id)) | length == 1 and
        (.[0].class | startswith("Arrows"))' >/dev/null || test_fails Initial class by racer.list
curl_postj action.php "action=racer.edit&racer=$(racerid_of 405)&firstname=Zuzu&lastname=Zingelo&carno=999&carname=Z-Car&rankid=$(rankid_of Webelos)&note_from=Suburbia" | check_jsuccess

# Look the racer up by its new car number, and check that it is still the racer
# we edited
function edited_racer_has() {
    curl_getj "action.php?query=racer.list" | \
        jq -e --argjson id $EDITED_RACERID \
           ".racers | map(select(.carnumber == 999)) | length == 1 and .[0].racerid == \$id and
            (.[0] | $1)" >/dev/null
}
edited_racer_has '.firstname == "Zuzu"' || test_fails Firstname change by racer.list
edited_racer_has '.lastname == "Zingelo"' || test_fails Lastname change by racer.list
edited_racer_has '.class | startswith("Webelos")' || test_fails Class change by racer.list
edited_racer_has '.note_from == "Suburbia"' || test_fails note_from change by racer.list
curl_getj "action.php?query=racer.list" | \
    jq -e '.racers | map(select(.note_from == null)) | length == 0' >/dev/null || \
    test_fails Racers without a note by racer.list

### Overwriting manual heat results: Clobber Dereck Dreier's results to all be 8.888
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=1&now_racing=0" | check_jsuccess
curl_postj action.php "action=result.write&lane2=8.888" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=2" | check_jsuccess
curl_postj action.php "action=result.write&lane4=8.888" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=3" | check_jsuccess
curl_postj action.php "action=result.write&lane1=8.888" | check_jsuccess
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&heat=4" | check_jsuccess
curl_postj action.php "action=result.write&lane3=8.888" | check_jsuccess

# For roundid 4, schedule two appearances per lane per racer
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Webelos 1)&n_times_per_lane=2" | check_jsuccess
# Schedule for roundid 5
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Arrows 1)" | check_jsuccess

### Racing for the first round of Webelos
curl_postj action.php "action=heat.select&roundid=$(roundid_of Webelos 1)&now_racing=1" | check_jsuccess
user_login_timer
curl_post action.php "action=timer-message&message=HEARTBEAT" | check_success

ROUND=$(roundid_of Webelos 1)
run_heat $ROUND 1  999:3.5819 510:3.3400 502:2.7394 null:3.9177
run_heat $ROUND 2  502:3.4442 999:2.4411 510:3.6294 null:2.4309
run_heat $ROUND 3  510:3.4408 502:3.6778 999:2.9768 null:3.7326
run_heat $ROUND 4  510:3.4894 502:2.0477 999:2.4732 null:2.5479
run_heat $ROUND 5  999:2.4360 502:3.4858 510:2.7608 null:3.4163
run_heat $ROUND 6  502:3.6268 510:2.5541 999:2.5365 null:3.9059
run_heat $ROUND 7  510:2.5955 999:3.7658 502:3.7152 null:3.4789
run_heat $ROUND 8  999:3.6487 502:3.0060 510:3.9589 null:3.6175  x

### Racing for the first round of Arrows
user_login_coordinator
curl_postj action.php "action=heat.select&roundid=$(roundid_of Arrows 1)&now_racing=1" | check_jsuccess
user_login_timer
curl_post action.php "action=timer-message&message=HEARTBEAT" | check_success

ROUND=$(roundid_of Arrows 1)
run_heat $ROUND 1  415:3.9962 445:3.9847 435:2.1091 425:3.2685
run_heat $ROUND 2  425:2.4600 415:3.6349 445:2.3152 435:2.6711
run_heat $ROUND 3  435:3.8841 425:2.8243 415:2.7381 445:3.9018
run_heat $ROUND 4  445:2.7886 435:3.5121 425:3.8979 415:2.0171  x

# We changed class 2 (White's Wolves) to have 4 trophies, not the default 3
curl_getj "action.php?query=award.list" | \
    jq -e --argjson white $(classid_of White) '.["speed-awards"] | map(select(.classid == $white)) | length == 4' > /dev/null || test_fails
curl_getj "action.php?query=award.list" | \
    jq -e --argjson lions $(classid_of Lions) '.["speed-awards"] | map(select(.classid == $lions)) | length == 3' > /dev/null || test_fails
curl_getj "action.php?query=award.list" | \
    jq -e --argjson bears $(classid_of Bears) '.["speed-awards"] | map(select(.classid == $bears)) | length == 3' > /dev/null || test_fails
curl_getj "action.php?query=award.list" | \
    jq -e --argjson webelos $(classid_of Webelos) '.["speed-awards"] | map(select(.classid == $webelos)) | length == 3' > /dev/null || test_fails
curl_getj "action.php?query=award.list" | \
    jq -e --argjson arrows $(classid_of Arrows) '.["speed-awards"] | map(select(.classid == $arrows)) | length == 3' > /dev/null || test_fails

user_login_coordinator

curl_postj action.php "action=award.present&key=speed-1" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Elliot
curl_postj action.php "action=award.present&key=speed-2" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Kris
curl_postj action.php "action=award.present&key=speed-3" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Blake

curl_postj action.php "action=award.present&key=speed-1-$(classid_of Webelos)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Kaba
curl_postj action.php "action=award.present&key=speed-2-$(classid_of Webelos)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Zuzu
curl_postj action.php "action=award.present&key=speed-3-$(classid_of Webelos)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Byron

# Make sure that excluding Carroll Cybulski leaves Adolpho Asher as the second-in-tigers winner
curl_postj action.php "action=award.present&key=speed-2-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Asher

# Issue#87: make sure the speed awards are there for the awards presentation page,
# which is built from award.list
curl_getj "action.php?query=award.list" | \
    jq -e '.classes | map(select(.name == "Lions & Tigers")) | length == 1' >/dev/null || \
    test_fails Lions \& Tigers in award.list
curl_getj "action.php?query=award.list" | \
    jq -e '.["speed-awards"] | map(select(.place == 3)) | length == 6' >/dev/null || \
    test_fails 3rd place speed awards by award.list

curl_postj action.php "action=settings.write&one-trophy-per=1&one-trophy-per-checkbox" | check_jsuccess
# There are only 3 racers in Webelos, and one of them gets a pack-level award.  So only
# five 3rd-place trophies in this case.
curl_getj "action.php?query=award.list" | \
    jq -e '.["speed-awards"] | map(select(.place == 3)) | length == 5' >/dev/null || \
    test_fails 3rd place speed awards with one trophy per racer, by award.list

curl_postj action.php "action=award.present&key=speed-1" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Elliot
curl_postj action.php "action=award.present&key=speed-2" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Kris
curl_postj action.php "action=award.present&key=speed-3" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Blake

curl_postj action.php "action=award.present&key=speed-1-$(classid_of Webelos)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Zuzu
curl_postj action.php "action=award.present&key=speed-2-$(classid_of Webelos)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Byron

# Issue#97: confirm lane bias calculation isn't broken
curl_page history.php | page_text | expect_sentence "1.757 sample variance."
curl_page history.php | page_text | expect_sentence "1.867 sample variance."
curl_page history.php | page_text | expect_sentence "1.832 sample variance."
curl_page history.php | page_text | expect_sentence "1.966 sample variance."
curl_page history.php | page_text | expect_sentence "F statistic is 0.090 with df1=3 and df2=85"

