#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# Each racer and the round are named by car number and looked up, because the
# Django server never reuses an id after a reset.

user_login_coordinator

RESET_SOURCE=single-group `dirname $0`/reset-database.sh "$BASE_URL"

curl_postj action.php "action=racer.import&firstname=Adolfo \"Dolf\"&lastname=Asher&carnumber=101" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Angelo&lastname=Alas&carnumber=202" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Antoine&lastname=Akiyama&&carnumber=303" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Arden&lastname=Aziz&carnumber=504" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Barney&lastname=Bainter&carnumber=405" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Ben&lastname=Bittinger&carnumber=106" | check_jsuccess
curl_postj action.php "action=racer.import&first-last=Blake Burling&carnumber=207" | check_jsuccess
curl_postj action.php "action=racer.import&first-last= Bruce  Boissonneault &carnumber=308" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Byron&lastname=Billy&carnumber=509" | check_jsuccess
curl_postj action.php "action=racer.import&first-last=Craney, Carey&carnumber=410" | check_jsuccess
curl_postj action.php "action=racer.import&first-last=Cybulski,Carroll&carnumber=111" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Christoper&lastname=Chauncey&carnumber=212" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Clark&lastname=Chesnutt&carnumber=313" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Cletus&lastname=Creager&carnumber=514" | check_jsuccess


curl_postj action.php "action=racer.pass&racer=$(racerid_of 101)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 202)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 303)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 504)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 405)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 106)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 207)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 308)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 509)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 410)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 111)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 212)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 313)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 514)&value=1" | check_jsuccess

curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess

# The one round there is
ROUND=$(lookup_get "action.php?query=poll&values=rounds" | jq '.rounds[0].roundid')
curl_postj action.php "action=schedule.generate&roundid=$ROUND" | check_jsuccess

curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
