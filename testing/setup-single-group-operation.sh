#! /bin/bash

# Sets up one group of 14 racers, all checked in, with the first heat racing.
# The racers and the round are looked up, because the Django server never
# reuses an id after a reset.  The check-ins go in import order, so on PHP the
# requests are racer=1 to racer=14, as the golden capture (08-suite-scripts.sh)
# recorded them.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

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


for RACERID in $(racerids_of 101 202 303 504 405 106 207 308 509 410 111 212 313 514) ; do
    curl_postj action.php "action=racer.pass&racer=$RACERID&value=1" | check_jsuccess
done

curl_postj action.php "action=settings.write&unused-lane-mask=0&n-lanes=4" | check_jsuccess

ROUND=$(the_only_roundid)
curl_postj action.php "action=schedule.generate&roundid=$ROUND" | check_jsuccess

curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
