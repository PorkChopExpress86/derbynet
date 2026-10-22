#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=aggregate-nonracing `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"

curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess

curl_postj action.php "action=class.edit&classid=$(classid_of Lions)&name=Tiger%20A" | check_jsuccess
curl_postj action.php "action=class.add&name=Tiger%20B" | check_jsuccess
curl_postj action.php "action=class.add&constituent_$(classid_of "Tiger A")=1&constituent_$(classid_of "Tiger B")=1&name=Tigers" | check_jsuccess

curl_postj action.php "action=racer.bulk&who=all&what=checkin&value=1" | check_jsuccess

curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 106)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 116)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 126)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 136)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 146)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 156)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 166)" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of "Tiger B")&racerid=$(racerid_of 176)" | check_jsuccess

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Tiger A" 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Tiger B" 1)" | check_jsuccess

ROUND=$(roundid_of "Tiger A" 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
run_heat $ROUND 1  101:3.488 121:3.656 141:3.360 181:3.518
run_heat $ROUND 2  111:3.393 131:3.273 151:3.346 101:3.293
run_heat $ROUND 3  121:3.339 141:3.698 161:3.844 111:3.465
run_heat $ROUND 4  131:3.260 151:3.351 171:3.441 121:3.672
run_heat $ROUND 5  141:3.738 161:3.392 181:3.717 131:3.682
run_heat $ROUND 6  151:3.116 171:3.591 101:3.553 141:3.800
run_heat $ROUND 7  161:3.026 181:3.381 111:3.464 151:3.843
run_heat $ROUND 8  171:3.233 101:3.127 121:3.630 161:3.694
run_heat $ROUND 9  181:3.845 111:3.144 131:3.859 171:3.090 x

ROUND=$(roundid_of "Tiger B" 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
run_heat $ROUND 1  106:3.488 126:3.656 146:3.360 176:3.518
run_heat $ROUND 2  116:3.393 136:3.273 156:3.346 106:3.293
run_heat $ROUND 3  126:3.339 146:3.698 166:3.844 116:3.465
run_heat $ROUND 4  136:3.260 156:3.351 176:3.441 126:3.672
run_heat $ROUND 5  146:3.738 166:3.392 106:3.717 136:3.682
run_heat $ROUND 6  156:3.116 176:3.591 116:3.553 146:3.800
run_heat $ROUND 7  166:3.026 106:3.381 126:2.464 156:2.843
run_heat $ROUND 8  176:3.233 116:3.127 136:3.630 166:3.694  x

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
ROUND=$(roundid_of White 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
run_heat $ROUND 1   202:3.711 212:3.108 227:3.420 247:3.463
run_heat $ROUND 2   207:3.397 217:3.019 232:3.792 252:3.081
run_heat $ROUND 3   212:3.710 222:3.874 237:3.474 257:3.757
run_heat $ROUND 4   217:3.140 227:3.717 242:3.816 262:3.197
run_heat $ROUND 5   222:3.104 232:3.395 247:3.402 267:3.868
run_heat $ROUND 6   227:3.788 237:3.473 252:3.666 272:3.803
run_heat $ROUND 7   232:3.325 242:3.485 257:3.645 277:3.643
run_heat $ROUND 8   237:3.878 247:3.302 262:3.033 282:3.393
run_heat $ROUND 9   242:3.751 252:3.123 267:3.489 202:3.344
run_heat $ROUND 10  247:3.178 257:3.648 272:3.843 207:3.805
run_heat $ROUND 11  252:3.647 262:3.093 277:3.248 212:3.845
run_heat $ROUND 12  257:3.085 267:3.576 282:3.474 217:3.451
run_heat $ROUND 13  262:3.563 272:3.702 202:3.107 222:3.841
run_heat $ROUND 14  267:3.488 277:3.266 207:3.650 227:3.687
run_heat $ROUND 15  272:3.245 282:3.477 212:3.846 232:3.571
run_heat $ROUND 16  277:3.291 202:3.811 217:3.786 237:3.189
run_heat $ROUND 17  282:3.642 207:3.182 222:3.571 242:3.311 x

curl_postj action.php "action=class.add&constituent_$(classid_of White)=1&constituent_$(classid_of Tigers)=1&name=Younger" | check_jsuccess

curl_getj "action.php?query=poll.coordinator" | \
    jq -e --argjson a $(classid_of Tigers) --argjson b $(classid_of Younger) \
       '.["ready-aggregate"] | map(.classid) | sort == ([$a, $b] | sort)' >/dev/null || test_fails

exit

curl_postj action.php "action=roster.new&classid=$(classid_of Younger)&top=4&bucketed=1" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[101,126,156,171,217,247,262,277]'

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Younger 1)" | check_jsuccess
