#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=aggregate-rounds `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"

# Turn on subgroups and split "Lions & Tigers" into 3 subgroups: "Lions"(1), "Tigers"(6), and "Cougars"(7)
curl_postj action.php "action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox" | check_jsuccess
curl_postj action.php "action=rank.edit&name=Lions&rankid=$(rankid_of Lions)" | check_jsuccess
curl_postj action.php "action=rank.add&classid=$(classid_of Lions)&name=Tigers" | check_jsuccess
curl_postj action.php "action=rank.add&classid=$(classid_of Lions)&name=Cougars" | check_jsuccess

# Move some Lions & Tigers to Tigers (rankid = 6)
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 106)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 116)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 126)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 146)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 156)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 171)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 176)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Tigers)&racer=$(racerid_of 181)" | check_jsuccess

# And 3 "Cougars"
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Cougars)&racer=$(racerid_of 121)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Cougars)&racer=$(racerid_of 136)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Cougars)&racer=$(racerid_of 141)" | check_jsuccess


`dirname $0`/run-scout-heats.sh "$BASE_URL"

# Add a Grand Final by ranks, leaving out the Lions subgroup
RANK_FINAL=`mktemp`
curl_postj action.php "action=roster.new&top=4&bucketed=1&rankid_$(rankid_of White)=1&rankid_$(rankid_of Bears)=1&rankid_$(rankid_of Webelos)=1&rankid_$(rankid_of Arrows)=1&rankid_$(rankid_of Tigers)=1&rankid_$(rankid_of Cougars)=1&classname=Rank%20Final" | tee $RANK_FINAL | check_jsuccess
finalists_carnumbers < $RANK_FINAL | expect_eq '[106,116,121,126,136,141,146,217,247,262,277,318,358,363,378,420,435,445,450,502,504,514,515]'

RANK_FINAL_ROUNDID=$(jq '.["new-round"].roundid' $RANK_FINAL)
curl_postj action.php "action=roster.delete&roundid=$RANK_FINAL_ROUNDID" | check_jsuccess

## Create "Younger Finals" aggregate of roundid 1,2 and race the round
curl_postj action.php "action=roster.new&top=4&bucketed=1&classid_$(classid_of Lions)=1&classid_$(classid_of White)=1&classname=Younger%20Finals" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[101,111,126,131,217,247,262,277]'

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Younger Finals" 1)" | check_jsuccess
ROUND=$(roundid_of "Younger Finals" 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess


run_heat $ROUND 1  126:3.103 131:3.762 262:3.359 277:3.471
run_heat $ROUND 2  101:3.757 111:3.635 247:3.085 126:3.328
run_heat $ROUND 3  131:3.586 262:3.749 217:3.095 101:3.494
run_heat $ROUND 4  111:3.095 247:3.645 277:3.836 131:3.540
run_heat $ROUND 5  262:3.087 217:3.278 126:3.702 111:3.135
run_heat $ROUND 6  247:3.602 277:3.405 101:3.632 262:3.523
run_heat $ROUND 7  217:3.685 126:3.136 131:3.737 247:3.419
run_heat $ROUND 8  277:3.000 101:3.477 111:3.670 217:3.512  x


## Create "Older Finals" aggregate of roundid 3,4,5, and race
curl_postj action.php "action=roster.new&top=4&bucketed=1&classid_$(classid_of Bears)=1&classid_$(classid_of Webelos)=1&classid_$(classid_of Arrows)=1&classname=Older%20Finals" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[318,358,363,378,420,435,445,450,502,504,514,515]'


curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Older Finals" 1)" | check_jsuccess
ROUND=$(roundid_of "Older Finals" 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess

run_heat $ROUND 1   358:3.756 318:3.763 515:3.380 435:3.056
run_heat $ROUND 2   363:3.450 378:3.692 502:3.079 358:3.329
run_heat $ROUND 3   318:3.003 514:3.790 420:3.470 363:3.199
run_heat $ROUND 4   378:3.563 504:3.857 445:3.255 318:3.742
run_heat $ROUND 5   514:3.850 515:3.145 450:3.849 378:3.288
run_heat $ROUND 6   504:3.706 502:3.212 435:3.343 514:3.711
run_heat $ROUND 7   515:3.282 420:3.510 358:3.703 504:3.653
run_heat $ROUND 8   502:3.785 445:3.835 363:3.264 515:3.024
run_heat $ROUND 9   420:3.081 450:3.502 318:3.650 502:3.256
run_heat $ROUND 10  445:3.120 435:3.286 378:3.209 420:3.890
run_heat $ROUND 11  450:3.352 358:3.673 514:3.355 445:3.039
run_heat $ROUND 12  435:3.110 363:3.026 504:3.401 450:3.152 x


## Race a second round of Older Finals
curl_postj action.php "action=roster.new&top=8&roundid=$(roundid_of "Older Finals" 1)" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[363,378,420,435,445,450,502,515]'

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Older Finals" 2)" | check_jsuccess
ROUND=$(roundid_of "Older Finals" 2)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess

run_heat $ROUND 1  435:3.862 363:3.887 502:3.162 420:3.234
run_heat $ROUND 2  515:3.329 445:3.401 378:3.554 435:3.015
run_heat $ROUND 3  363:3.674 502:3.837 450:3.102 515:3.500
run_heat $ROUND 4  445:3.792 378:3.685 420:3.096 363:3.530
run_heat $ROUND 5  502:3.819 450:3.501 435:3.624 445:3.451
run_heat $ROUND 6  378:3.183 420:3.007 515:3.227 502:3.665
run_heat $ROUND 7  450:3.370 435:3.512 363:3.448 378:3.486
run_heat $ROUND 8  420:3.028 515:3.104 445:3.049 450:3.301 x

## Create a final final of the other two GF's
curl_postj action.php "action=roster.new&top=4&bucketed=1&classid_$(classid_of "Older Finals")=1&classid_$(classid_of "Younger Finals")=1&classname=Final%20Finals" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[111,126,217,277,420,445,450,515]'

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Final Finals" 1)" | check_jsuccess
ROUND=$(roundid_of "Final Finals" 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess

run_heat $ROUND 1  420:3.061 450:3.857 126:3.565 277:3.674
run_heat $ROUND 2  515:3.825 445:3.672 111:3.592 420:3.106
run_heat $ROUND 3  450:3.392 126:3.409 217:3.651 515:3.577
run_heat $ROUND 4  445:3.65 111:3.132 277:3.415 450:3.85
run_heat $ROUND 5  126:3.895 217:3.052 420:3.636 445:3.096
run_heat $ROUND 6  111:3.598 277:3.415 515:3.625 126:3.831
run_heat $ROUND 7  217:3.059 420:3.54 450:3.306 111:3.714
run_heat $ROUND 8  277:3.563 515:3.657 445:3.648 217:3.895  x


