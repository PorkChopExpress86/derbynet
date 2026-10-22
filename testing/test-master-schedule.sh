#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

curl_postj action.php "action=settings.write&use-master-sched-checkbox=1&use-master-sched=1" | check_jsuccess
curl_postj action.php "action=settings.write&n-lanes=4" | check_jsuccess

# Two racers from Bears & Freres (but 4 heats, with byes)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 303)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 308)&value=1" | check_jsuccess

# All the Lions and Tigers (17)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 101)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 106)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 111)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 116)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 126)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 131)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 136)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 141)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 146)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 151)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 156)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 161)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 171)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 166)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 176)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 181)&value=1" | check_jsuccess

# White's Wolves (13)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 202)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 207)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 212)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 222)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 217)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 227)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 232)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 237)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 242)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 252)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 262)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 272)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 282)&value=1" | check_jsuccess

# Three racers from Webelos (4 heats, with byes)
curl_postj action.php "action=racer.pass&racer=$(racerid_of 508)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 509)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 510)&value=1" | check_jsuccess

# ... for a total of 38 heats
# 17/4 ~ 4 ratio for Lions & Tigers
# 13/4 ~ 3 ratio for Whites's Wolves
# 4/4 = 1 ratio for Bears & Freres
# 4/4 = 1 ratio for Webelos

# Schedule dens
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Lions 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of White 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Bears 1)" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Webelos 1)" | check_jsuccess
# Can't schedule Arrows, because no one's checked in
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Arrows 1)" | check_jfailure

curl_getj "action.php?query=poll.coordinator" | \
    jq --argjson lions_r1 $(roundid_of Lions 1) ".[\"current-heat\"] | .[\"now_racing\"] == false and .roundid == \$lions_r1" | \
    expect_eq true

curl_postj action.php "action=heat.select&heat=next-up&now_racing=0" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess

curl_getj "action.php?query=poll.coordinator" | \
    jq --argjson lions_r1 $(roundid_of Lions 1) ".[\"current-heat\"] | .[\"now_racing\"] == true and .roundid == \$lions_r1 and .heat == 1" | \
    expect_eq true

## This script generated from the output of:
## timer/testing/fake-timer -t -l 4 localhost/xsite

user_login_timer
curl_post action.php "action=timer-message&message=HELLO" | check_success
curl_post action.php "action=timer-message&message=IDENTIFIED&nlanes=4" | check_success
cat $DEBUG_CURL | expect_one "<heat-ready[ />]"

ROUND=$(roundid_of Lions 1)
run_heat $ROUND 1   101:2.6158 111:3.7824 126:2.0463 146:2.0036
run_heat $ROUND 2   106:2.1873 116:2.7640 131:3.2498 151:2.7619
run_heat $ROUND 3   111:3.5283 121:3.2649 136:2.3900 156:2.1653
run_heat $ROUND 4   116:3.6831 126:3.9044 141:3.2217 161:3.2740
ROUND=$(roundid_of White 1)
run_heat $ROUND 1   202:3.9820 212:2.3631 232:2.6738 227:3.0597
run_heat $ROUND 2   207:2.5833 217:3.5019 237:2.9375 232:3.7031
run_heat $ROUND 3   212:3.7282 222:2.7925 242:2.9317 237:3.4691
run_heat $(roundid_of Bears 1) 1   303:3.6444 -          -          308:2.4671
run_heat $(roundid_of Webelos 1) 1   508:2.8700 -          510:2.2015 509:2.8728
ROUND=$(roundid_of Lions 1)
run_heat $ROUND 5   121:2.9534 131:2.2606 146:3.6678 166:2.6781
run_heat $ROUND 6   126:3.8819 136:3.3501 151:3.7373 171:3.1624
run_heat $ROUND 7   131:3.2352 141:2.7528 156:3.6898 176:2.7172
run_heat $ROUND 8   136:2.0539 146:3.8099 161:3.8300 181:2.5309
ROUND=$(roundid_of White 1)
run_heat $ROUND 4   217:3.5253 227:3.0825 252:2.0197 242:2.0079
run_heat $ROUND 5   222:3.2461 232:2.3070 262:2.7460 252:3.1656
run_heat $ROUND 6   227:3.9903 237:2.1272 272:2.9032 262:2.3380
run_heat $(roundid_of Bears 1) 2   308:3.4030 303:3.9654 -          -
run_heat $(roundid_of Webelos 1) 2   509:2.5979 508:3.8489 -          510:2.7025
ROUND=$(roundid_of Lions 1)
run_heat $ROUND 9   141:3.5098 151:2.9721 166:2.7074 101:2.6940
run_heat $ROUND 10  146:3.4633 156:2.2940 171:3.8348 106:2.1791
run_heat $ROUND 11  151:2.4415 161:2.3792 176:3.8497 111:3.1925
run_heat $ROUND 12  156:3.2020 166:3.7824 181:2.0173 116:3.4068
ROUND=$(roundid_of White 1)
run_heat $ROUND 7   232:2.7497 242:2.1385 282:3.6122 272:2.5128
run_heat $ROUND 8   237:3.2886 252:3.5359 202:3.6490 282:2.4214
run_heat $ROUND 9   242:2.4732 262:3.8659 207:3.7021 202:2.7243
run_heat $(roundid_of Bears 1) 3   -          308:3.9649 303:3.7225 -
run_heat $(roundid_of Webelos 1) 3   510:3.3670 509:3.0116 508:2.9083 -
ROUND=$(roundid_of Lions 1)
run_heat $ROUND 13  161:2.6151 171:2.8070 101:2.3636 121:3.6158
run_heat $ROUND 14  166:2.0063 176:3.7848 106:3.6190 126:2.4874
run_heat $ROUND 15  171:2.0059 181:3.5972 111:3.8646 131:3.9890
run_heat $ROUND 16  176:2.5804 101:3.2428 116:2.0773 136:2.5551
ROUND=$(roundid_of White 1)
run_heat $ROUND 10  252:3.4255 272:3.4075 212:3.0036 207:3.3740
run_heat $ROUND 11  262:3.3379 282:3.6183 217:3.2268 212:2.7605
run_heat $ROUND 12  272:3.0604 202:3.1527 222:2.5206 217:2.0743
run_heat $(roundid_of Bears 1) 4   -          -          308:2.2914 303:2.1275
run_heat $(roundid_of Webelos 1) 4   -          510:3.7456 509:3.1559 508:2.8862
run_heat $(roundid_of Lions 1) 17  181:3.0536 106:2.1045 121:2.2103 141:3.5851
run_heat $(roundid_of White 1) 13  282:3.9767 207:3.6456 227:2.2947 222:3.7196  x

curl_getj "action.php?query=poll.coordinator" | \
    jq ".[\"current-heat\"] | .[\"now_racing\"] == false" | \
    expect_eq true

user_login_coordinator
# Now create a aggregate round, 3 from each den.
# (Bears/Freres have only 2 racers and Webelos only 3.)
curl_postj action.php "action=roster.new&roundid=&top=3&bucketed=1&classid_$(classid_of Lions)=1&classid_$(classid_of White)=1&classid_$(classid_of Bears)=1&classid_$(classid_of Webelos)=1&classname=Grand%20Finals" \
    | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | expect_eq '[101,106,136,232,237,242,303,308,508,509,510]'

curl_getj "action.php?query=poll.coordinator" | \
    jq --argjson white_r1 $(roundid_of White 1) ".[\"current-heat\"] | .[\"now_racing\"] == false and .roundid == \$white_r1 and .heat == 13" | \
    expect_eq true

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "Grand Finals" 1)" | check_jsuccess

curl_getj "action.php?query=poll.coordinator" | \
    jq --argjson grand_finals_r1 $(roundid_of "Grand Finals" 1) ".[\"current-heat\"] | .[\"now_racing\"] == false and .roundid == \$grand_finals_r1 and .heat == 1" | \
    expect_eq true

curl_postj action.php "action=heat.select&heat=next-up&now_racing=0" | check_jsuccess
curl_postj action.php "action=heat.select&now_racing=1" | check_jsuccess


run_heat $(roundid_of "Grand Finals" 1) 1  106:3 101:2 232:1 508:4

# Next heat is Grand Finals heat 2
curl_getj "action.php?query=poll.coordinator" | \
    jq --argjson grand_finals_r1 $(roundid_of "Grand Finals" 1) ".[\"current-heat\"] | .[\"now_racing\"] == true and .roundid == \$grand_finals_r1 and .heat == 2" | \
    expect_eq true

curl_getj "action.php?query=class.list" | expect_one 'Grand Finals'

# Unschedule and remove Grand Finals round
GRAND_FINALS_CLASSID=$(classid_of "Grand Finals")
curl_postj action.php "action=result.delete&roundid=$(roundid_of "Grand Finals" 1)&heat=1" | check_jsuccess
curl_postj action.php "action=schedule.unschedule&roundid=$(roundid_of "Grand Finals" 1)" | check_jsuccess
curl_postj action.php "action=roster.delete&roundid=$(roundid_of "Grand Finals" 1)" | check_jsuccess

curl_getj "action.php?query=class.list" | expect_count 'Grand Finals' 0
curl_getj "action.php?query=class.list" | \
    jq --argjson grand_finals $GRAND_FINALS_CLASSID '.classes | all(.classid != $grand_finals)' | expect_eq true
