#! /bin/bash

# Runs an unattended demo of the software.  Stand up a web server somewhere, and
# a kiosk that points to it.  Run this script, pointed at the server.  It runs
# until stopped, against the PHP or the Django server.
#
# Each pass resets the database, so it asks for every id by name: the Django
# server never reuses an id, so the second pass gets new ones.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

while true ; do
  curl_postj action.php "action=database.execute&script=schema" | check_jsuccess
  curl_postj action.php "action=database.execute&script=update-schema" | check_jsuccess
  curl_postj action.php "action=settings.write&photos-on-now-racing=head" | check_jsuccess

  # kiosk page will re-poll every 5s
  sleep 6s
  ########## Please Check In ##############
  curl_postj action.php "action=kiosk.assign&all=kiosks/please-check-in.kiosk" | check_jsuccess

  `dirname $0`/import-roster.sh "$BASE_URL"
  # The photos test-photo-assignments.sh assigns.  Uploading them again replaces
  # them.
  `dirname $0`/photo-setup.sh "$BASE_URL"
  `dirname $0`/test-photo-assignments.sh "$BASE_URL"
  sleep 6s

  ########## Slideshow ##############
  curl_postj action.php "action=kiosk.assign&all=kiosks/slideshow.kiosk" | check_jsuccess

  curl_postj action.php "action=settings.write&n-lanes=4" | check_jsuccess

  `dirname $0`/checkin-all.sh "$BASE_URL"
  sleep 15s

  TIGERS=$(classid_of Lions)
  WOLVES=$(classid_of White)
  BEARS=$(classid_of Bears)
  WEBELOS_I=$(classid_of Webelos)
  WEBELOS_II=$(classid_of Arrows)
  curl_postj action.php "action=class.edit&classid=$TIGERS&name=Tigers" | check_jsuccess
  curl_postj action.php "action=class.edit&classid=$WOLVES&name=Wolves" | check_jsuccess
  curl_postj action.php "action=class.edit&classid=$BEARS&name=Bears" | check_jsuccess
  curl_postj action.php "action=class.edit&classid=$WEBELOS_I&name=Webelos%20I" | check_jsuccess
  curl_postj action.php "action=class.edit&classid=$WEBELOS_II&name=Webelos%20II" | check_jsuccess

  curl_postj action.php "action=class.order&classid_1=$TIGERS&classid_2=$WOLVES&classid_3=$BEARS&classid_4=$WEBELOS_I&classid_5=$WEBELOS_II" | check_jsuccess

  # Start the timer
  user_login_timer
  curl_post action.php "action=timer-message&message=HELLO" | check_success
  curl_post action.php "action=timer-message&message=IDENTIFIED&lane_count=4" | check_success

  user_login_coordinator
  # Schedule Round 1
  TIGERS_1=$(roundid_of Tigers 1)
  curl_postj action.php "action=schedule.generate&roundid=$TIGERS_1" | check_jsuccess
  curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Wolves 1)" | check_jsuccess

  ########## On Deck ##############
  curl_postj action.php "action=kiosk.assign&all=kiosks/ondeck.kiosk" | check_jsuccess

  # Start racing
  curl_postj action.php "action=heat.select&now_racing=1&roundid=$TIGERS_1" | check_jsuccess

  user_login_timer
  sleep 1s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.2514&lane2=3.9601&lane3=2.1068&lane4=2.4405" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.5577&lane2=2.2320&lane3=3.8407&lane4=3.2743" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.0326&lane2=2.1680&lane3=2.8041&lane4=2.7058" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.5629&lane2=2.3208&lane3=2.7011&lane4=2.9505" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.8318&lane2=2.6336&lane3=3.7829&lane4=3.8474" | check_success
  sleep 3s

  user_login_coordinator
  ########## Now-Racing ##############
  curl_postj action.php "action=kiosk.assign&all=kiosks/now-racing.kiosk" | check_jsuccess

  user_login_timer
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 4s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.8646&lane2=3.5764&lane3=3.8810&lane4=2.2240" | check_success
  sleep 15s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 4s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.4750&lane2=2.4742&lane3=2.8326&lane4=3.2385" | check_success
  sleep 15s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 4s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.3017&lane2=3.9341&lane3=3.0639&lane4=3.0011" | check_success
  sleep 15s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 4s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.2278&lane2=3.1430&lane3=2.3543&lane4=3.0481" | check_success
  sleep 15s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 4s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.8930&lane2=3.9921&lane3=3.8794&lane4=2.8338" | check_success
  sleep 15s

  ########## Results-By-Racer ##############
  user_login_coordinator
  curl_postj action.php "action=kiosk.assign&all=kiosks/results-by-racer.kiosk" | check_jsuccess
  user_login_timer

  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.7335&lane2=2.4780&lane3=3.5128&lane4=2.3030" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.5309&lane2=3.6360&lane3=3.0660&lane4=2.8207" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.7994&lane2=3.3811&lane3=2.6539&lane4=3.2623" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=3.5108&lane2=3.0641&lane3=3.6981&lane4=2.9022" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.6329&lane2=2.2925&lane3=2.2111&lane4=3.8931" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.7492&lane2=2.6261&lane3=3.9556&lane4=3.5883" | check_success
  sleep 3s
  curl_post action.php "action=timer-message&message=STARTED" | check_success
  sleep 2s
  curl_post action.php "action=timer-message&message=FINISHED&lane1=2.6295&lane2=3.1224&lane3=2.8310&lane4=2.7356" | check_success
  sleep 3s

  ########## Awards Presentations ##############
  # The timer can't assign a kiosk or present an award, on either server.
  user_login_coordinator
  curl_postj action.php "action=kiosk.assign&all=kiosks/award-presentations.kiosk" | check_jsuccess
  sleep 6s
  curl_postj action.php "action=award.present&key=speed-1-$TIGERS" | check_jsuccess
  sleep 15s

  ########## DerbyNet ##############
  curl_postj action.php "action=kiosk.assign&all=kiosks/derbynet.kiosk" | check_jsuccess
  sleep 30s
done
