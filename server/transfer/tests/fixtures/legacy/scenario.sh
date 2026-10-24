#! /bin/bash
#
# Fills the PHP server's event database for the v10 fixture.  build.sh runs it
# in the harness image on the server's network, from the repository root:
#
#   scenario.sh <base url> <photo dir>
#
# <photo dir> holds the small JPEGs that build.sh made.  The roster and the
# racing come from testing/setup-basic-no-photos.sh and run-scout-heats.sh.  The
# rest adds rows to legacy tables that those two leave empty.

BASE_URL=$1
PHOTOS=$2
set -e -E -o pipefail
export PASSWORDS_FILE=testing/default.passwords
source testing/common.sh

# A fresh server has no database yet, so the login fails and setup.nodata makes
# one, as exercise-one-database.sh does.
curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
     -d "action=role.login&name=RaceCoordinator&password=$(sed -n -e 's/^RaceCoordinator://p' $PASSWORDS_FILE)" \
     > /dev/null
curl_postj action.php "action=setup.nodata&ez-new=testdb" > /dev/null

testing/setup-basic-no-photos.sh "$BASE_URL"

# racer.pass records checkin_time, which run-scout-heats.sh's racer.bulk does
# not.  Several within one second give tied Events rows at older schemas.
user_login_coordinator
for car in 101 111 121 131 141 202 212; do
    curl_postj action.php "action=racer.pass&racer=$(racerid_of $car)&value=1" | check_jsuccess
done

testing/run-scout-heats.sh "$BASE_URL"

user_login_coordinator

upload() {
    # $1 repo, $2 file, $3 car number
    curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
         -X POST -F MAX_FILE_SIZE=2000000 -F photo="@$2" -F action=photo.upload \
         -F repo=$1 -F racerid=$(racerid_of $3) | check_jsuccess
}
upload head "$PHOTOS/head-101.jpg" 101
upload head "$PHOTOS/head-111.jpg" 111
upload car "$PHOTOS/car-101.jpg" 101
curl_postj action.php "action=photo.crop&repo=head&image_name=head-101.jpg&left=0&top=0&right=8&bottom=8&original_width=16&original_height=16" | check_jsuccess

# An aggregate of two dens, and one of two subgroups.
curl_postj action.php "action=class.add&constituent_$(classid_of Lions)=1&constituent_$(classid_of White)=1&name=Younger" | check_jsuccess
curl_postj action.php "action=class.add&rankid_$(rankid_of Bears)=1&rankid_$(rankid_of Webelos)=1&name=Bigger" | check_jsuccess

# Awards: pack-wide with a winner, per group, per subgroup, and on the ballot.
curl_postj action.php "action=award.import&awardname=Best%20in%20Show&awardtype=Design%20Trophy&carnumber=101&sort=1" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=1&name=Most%20Lionic&class_and_rank=$(classid_of Lions),0&sort=2" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=3&name=Best%20Bear&class_and_rank=$(classid_of Bears),$(rankid_of Bears)&sort=3" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Best in Show")&ballot_depth=2" | check_jsuccess
curl_getj "action.php?query=ballot.get&password=" > /dev/null
curl_postj action.php "action=ballot.open&state=open" | check_jsuccess
curl_postj action.php "action=vote.cast&awardid=$(awardid_of "Best in Show")&votes=[$(racerid_of 111),$(racerid_of 121)]" | check_jsuccess
curl_postj action.php "action=ballot.open&state=closed" | check_jsuccess

# Playlist entries, one that shows a scene at the finish.
curl_postj action.php "action=playlist.add&classid=$(classid_of Lions)&round=2&sceneid_at_finish=5" | check_jsuccess
curl_postj action.php "action=playlist.add&classid=$(classid_of Bears)&round=2&n_times_per_lane=1&continue_racing=1" | check_jsuccess

# Kiosks: one with parameters, one on a local page, one waiting for a reload.
for k in 1 2 3; do
    curl_getj "action.php?query=poll.kiosk&address=fixture-kiosk-$k" > /dev/null
done
curl_postj action.php "action=kiosk.assign&address=fixture-kiosk-1&name=Main" | check_jsuccess
curl_postj action.php "action=kiosk.assign&address=fixture-kiosk-1&page=kiosks/please-check-in.kiosk&params=%7B%22classids%22%3A%5B1%2C2%5D%7D" | check_jsuccess
curl_postj action.php "action=kiosk.assign&address=fixture-kiosk-2&page=local/kiosks/feedback.kiosk" | check_jsuccess
curl_postj action.php "action=kiosk.assign&address=fixture-kiosk-3&page=kiosks/standings.kiosk" | check_jsuccess
# kiosk.reload reports no outcome.
curl_postj action.php "action=kiosk.reload&address=fixture-kiosk-3" > /dev/null
curl_postj action.php "action=scene.setkiosk&sceneid=5&kiosk_name=Main&page=kiosks/award-presentations.kiosk&params=%7B%22confetti%22%3Afalse%7D" | check_jsuccess
