#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# expect_points <carnumber> <heats> <points>
expect_points() {
    standings_row Pack $1 | \
        jq -e --argjson h $2 --argjson p $3 \
           '.Heats == $h and (to_entries | map(select(.key | startswith("Total Points")))[0].value) == $p' \
           >/dev/null || test_fails Car $1 should have $2 heats and $3 points
}

user_login_coordinator

RESET_SOURCE=points-racing `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"
### Check in every other racer...
`dirname $0`/test-basic-checkins.sh "$BASE_URL"

# Reinstate car 111
curl_postj action.php "action=racer.edit&racer=$(racerid_of 111)&firstname=Carroll&lastname=Cybulski&carno=111&carname=Vroom&rankid=$(rankid_of Lions)&exclude=0" | check_jsuccess

curl_postj action.php "action=settings.write&use-points=1&use-points-checkbox" | check_jsuccess
curl_postj action.php "action=settings.write&n-lanes=4" | check_jsuccess

### Schedule roundid 1
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
# Racing for the first round of Lions & Tigers: 5 heats
curl_postj action.php "action=heat.select&roundid=$(roundid_of Lions 1)&now_racing=1" | check_jsuccess

user_login_timer
curl_post action.php "action=timer-message&message=HELLO" | check_success
curl_post action.php "action=timer-message&message=IDENTIFIED&nlanes=4" | check_success

ROUND=$(roundid_of Lions 1)
run_heat -place $ROUND 1   101:2 121:3 141:4 111:1
run_heat -place $ROUND 2   111:1 131:4 101:2 121:3

# Report times for this heat, let timer-message compute places
# First we have a tie for last place in the heat, as for a couple of DNFs;
# both racers get 1 point each (tie for 3rd place)
run_heat $ROUND 3 121:9.9999 141:3.2 111:3.1 131:9.9999

user_login_coordinator
# Check the points for each racer in the heat: # heats, total points
expect_points 121 3 6
expect_points 141 2 4
expect_points 111 3 12
expect_points 131 2 3
curl_postj action.php "action=heat.rerun&heat=last" | check_jsuccess
user_login_timer

# Now re-run with a tie for first place
run_heat $(roundid_of Lions 1) 3  121:3.3 141:3.1 111:3.1 131:3.4

user_login_coordinator
expect_points 121 3 6
expect_points 141 2 5
expect_points 111 3 12
expect_points 131 2 2
curl_postj action.php "action=heat.rerun&heat=last" | check_jsuccess
user_login_timer

# Finally, re-run the heat again, now with four good times:
run_heat $(roundid_of Lions 1) 3  121:3.3 141:3.2 111:3.1 131:3.4

user_login_coordinator
expect_points 121 3 6
expect_points 141 2 4
expect_points 111 3 12
expect_points 131 2 2
user_login_timer

run_heat -place $(roundid_of Lions 1) 4   131:4 101:1 121:2 141:3

user_login_coordinator
curl_getj "action.php?query=poll.coordinator" | jq '.["last-heat"] == "available"' | expect_eq true
curl_postj action.php "action=heat.rerun&heat=last" | check_jsuccess
curl_getj "action.php?query=poll.coordinator" | \
    jq '.["last-heat"] == "recoverable" and 
        (.["heat-results"] | all(has("finishtime") and has("finishplace")))' | \
    expect_eq true

curl_postj action.php "action=heat.reinstate" | grep 'last[_-]heat' | expect_one none
curl_getj "action.php?query=poll.coordinator" | \
    jq '.racers | 
        all((.finishplace==1 and (.name | test("Adolfo.*"))) or 
            (.finishplace==2 and .name == "Derick Dreier") or 
            (.finishplace==3 and .name == "Jesse Jara") or 
            (.finishplace==4 and .name == "Felton Fouche"))' | \
    expect_eq true

curl_postj action.php "action=heat.select&heat=next&now_racing=1" | check_jsuccess
user_login_timer

run_heat -place $(roundid_of Lions 1) 5   141:2 111:1 131:4 101:3  x

# Results (place):
# 111 101 121 141
# 111 101 121 131
# 111 141 121 131
# 101 121 141 131
# 111 141 101 131
#
#  car           first         last      sum(finishplace)  points
# 111         Carroll        Cybulski     4                16
# 101         Adolfo "Dolf"  Asher        8                12
# 121         Derick         Dreier      11                 9
# 141         Jesse          Jara        11                 9
# 131         Felton         Fouche      16                 4

user_login_coordinator
curl_postj action.php "action=award.present&key=speed-2-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Asher

curl_getj "action.php?query=poll&values=ondeck" | \
    jq -r '.ondeck.chart | map(select(.resultid == 1))[0].result' | expect_eq z2nd
curl_getj "action.php?query=poll&values=ondeck" | \
    jq -r '.ondeck.chart | map(select(.resultid == 4))[0].result' | expect_eq z1st

# Test the tie in the standings
expect_places Pack Place 111 1 101 2 121 T3 141 T3 131 5
expect_row Pack 111 '[1,111,"Carroll Cybulski","Vroom","Lions & Tigers",1,4,16,"1st","1st"]'
expect_row Pack 101 '[2,101,"Adolfo \"Dolf\" Asher","","Lions & Tigers",2,4,12,"1st","3rd"]'
expect_row Pack 121 '["T3",121,"Derick Dreier","","Lions & Tigers","T3",4,9,"2nd","3rd"]'
expect_row Pack 141 '["T3",141,"Jesse Jara","","Lions & Tigers","T3",4,9,"2nd","4th"]'
expect_row Pack 131 '[5,131,"Felton Fouche","","Lions & Tigers",5,4,4,"4th","4th"]'

# award presentation when there's a tie for 3rd
curl_postj action.php "action=award.present&key=speed-3a-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Derick
curl_postj action.php "action=award.present&key=speed-3b-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Jesse

# There are only two tied for 3rd place, not a third
curl_postj action.php "action=award.present&key=speed-3c-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | jq 'length' | expect_eq 0

# There's no fourth place when there's a tie for 3rd
curl_postj action.php "action=award.present&key=speed-4-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | jq 'length' | expect_eq 0

# One-trophy-per-racer means Felton takes 1st place in Lions
curl_postj action.php "action=settings.write&one-trophy-per=1&one-trophy-per-checkbox" | check_jsuccess
curl_postj action.php "action=award.present&key=speed-3a" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Derick
curl_postj action.php "action=award.present&key=speed-3b" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Jesse
curl_postj action.php "action=award.present&key=speed-3c" | check_jsuccess
curl_getj "action.php?query=award.current" | jq 'length' | expect_eq 0
curl_postj action.php "action=award.present&key=speed-4" | check_jsuccess
curl_getj "action.php?query=award.current" | jq 'length' | expect_eq 0
curl_postj action.php "action=award.present&key=speed-1-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one Felton
curl_postj action.php "action=award.present&key=speed-2-$(classid_of Lions)" | check_jsuccess
curl_getj "action.php?query=award.current" | jq 'length' | expect_eq 0

# Generate a next round of top 3, where there's a tie for third -- take 4 finalists
curl_postj action.php "action=roster.new&roundid=$(roundid_of Lions 1)&top=3" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[101,111,121,141]' Expecting 4 finalists

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 2)" | check_jsuccess
# Racing for the second round of Lions & Tigers: 4 heats
ROUND=$(roundid_of Lions 2)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess

run_heat -place $ROUND 1   111:2 141:3 121:4 101:1
run_heat -place $ROUND 2   101:3 111:4 141:1 121:2
run_heat -place $ROUND 3   121:4 101:1 111:2 141:3
run_heat -place $ROUND 4   141:1 121:2 101:3 111:4  x

# Round 1's own standings, and which racers the pack-wide standings now attribute
# to a later round
expect_places "Lions & Tigers, Round 1" Place 111 1 101 2 121 T3 141 T3 131 5
standings_sheet "Lions & Tigers, Round 1" | jq -e 'length == 5' >/dev/null || \
    test_fails Round 1 standings should have 5 racers
standings_sheet Pack | jq -e 'map(.["Car Number"]) | sort == [101,111,121,141]' >/dev/null || \
    test_fails Only the finalists should appear in the pack standings

expect_row "Lions & Tigers, Round 1" 111 '[1,111,"Carroll Cybulski","Vroom","Lions & Tigers","",4,16,"1st","1st"]'
expect_row "Lions & Tigers, Round 1" 101 '[2,101,"Adolfo \"Dolf\" Asher","","Lions & Tigers","",4,12,"1st","3rd"]'
expect_row "Lions & Tigers, Round 1" 121 '["T3",121,"Derick Dreier","","Lions & Tigers","",4,9,"2nd","3rd"]'
expect_row "Lions & Tigers, Round 1" 141 '["T3",141,"Jesse Jara","","Lions & Tigers","",4,9,"2nd","4th"]'
expect_row "Lions & Tigers, Round 1" 131 '[5,131,"Felton Fouche","","Lions & Tigers","",4,4,"4th","4th"]'

# In the pack: Place is the pack-wide standing and "In Den" the standing in the round.
# In the group (Lions & Tigers): Place is the standing in the group.
expect_places Pack Place 101 T1 141 T1 111 T3 121 T3
expect_places Pack "In Den" 101 T1 141 T1 111 T3 121 T3
expect_places "Lions & Tigers" Place 101 T1 141 T1 111 T3 121 T3

expect_row Pack 101 '["T1",101,"Adolfo \"Dolf\" Asher","","Lions & Tigers","T1",4,12,"1st","3rd"]'
expect_row Pack 141 '["T1",141,"Jesse Jara","","Lions & Tigers","T1",4,12,"1st","3rd"]'
expect_row Pack 111 '["T3",111,"Carroll Cybulski","Vroom","Lions & Tigers","T3",4,8,"2nd","4th"]'
expect_row Pack 121 '["T3",121,"Derick Dreier","","Lions & Tigers","T3",4,8,"2nd","4th"]'
