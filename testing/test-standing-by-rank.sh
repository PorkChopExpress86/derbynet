#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=standing-by-rank `dirname $0`/reset-database.sh "$BASE_URL"

curl_postj action.php "action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox=1" | check_jsuccess

curl_postj action.php "action=partition.apply-rule&rule=one-group" | check_jsuccess

# Single group, ThePack
# Subgroup Lions = 1
# Subgroup Tigers = 2
# Subgroup Bears = 3
# Subgroup Wolves = 4

curl_postj action.php "action=racer.import&firstname=Jewell&lastname=Jeansonne&carnumber=101&partition=Lions&exclude=" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Cedrick&lastname=Charley&carnumber=202&partition=Tigers" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Grant&lastname=Gribble&carnumber=303&partition=Bears" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Harrison&lastname=Hanks&carnumber=404&partition=Wolves" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Jerald&lastname=Jerry&carnumber=105&partition=Lions&exclude=X" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Ernest&lastname=Edelman&carnumber=206&partition=Tigers" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Lloyd&lastname=Lightsey&carnumber=307&partition=Bears" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Gary&lastname=Grissom&carnumber=408&partition=Wolves" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Von&lastname=Vassar&carnumber=109&partition=Lions&exclude=NO" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Harold&lastname=Hayek&carnumber=210&partition=Tigers&exclude=yes" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Pasquale&lastname=Procopio&carnumber=311&partition=Bears" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Winford&lastname=Weld&carnumber=412&partition=Wolves" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=John&lastname=Jefferys&carnumber=113&partition=Lions&exclude=0" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Thanh&lastname=Turner&carnumber=214&partition=Tigers" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Demetrius&lastname=Demming&carnumber=315&partition=Bears" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Everette&lastname=Esses&carnumber=416&partition=Wolves" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Forrest&lastname=Figgins&carnumber=117&partition=Lions" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Sterling&lastname=Spalla&carnumber=218&partition=Tigers" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Delmar&lastname=Donnelly&carnumber=319&partition=Bears" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Stanton&lastname=Salmon&carnumber=420&partition=Wolves" | check_jsuccess

curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 101)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 202)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 303)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 404)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 105)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 206)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 307)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 408)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 109)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 210)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 311)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 412)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 113)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 214)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 315)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 416)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 117)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 218)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 319)" | check_jsuccess
curl_postj action.php "action=racer.pass&value=1&racer=$(racerid_of 420)" | check_jsuccess

curl_postj action.php "action=settings.write&n-lanes=4" | check_jsuccess
curl_postj action.php "action=schedule.generate&roundid=$(roundid_of "All Racers" 1)" | check_jsuccess

curl_postj action.php "action=heat.select&roundid=$(roundid_of "All Racers" 1)&now_racing=1" | check_jsuccess

# Fast Lions    1.100
# Slow Tigers   5.500
# Mixed Bears, Wolves 2.00, 2.50, 3.00

# Racing chart (1=Lions, etc.):

user_login_timer
curl_post action.php "action=timer-message&message=HELLO" | check_success
curl_post action.php "action=timer-message&message=IDENTIFIED&nlanes=4" | check_success
cat $DEBUG_CURL | expect_one "<heat-ready[ />]"

ROUND=$(roundid_of "All Racers" 1)
run_heat $ROUND  1  101:1.010 303:2.503 117:1.170 408:2.508
run_heat $ROUND  2  202:5.502 404:2.504 218:5.518 109:1.09
run_heat $ROUND  3  303:2.503 105:1.050 319:2.519 210:5.510
run_heat $ROUND  4  404:2.504 206:5.506 420:3.020 311:3.011
run_heat $ROUND  5  105:1.050 307:2.507 101:1.010 412:3.012
run_heat $ROUND  6  113:1.130 315:3.015 109:1.090 420:3.020
run_heat $ROUND  7  412:3.012 214:5.514 408:2.508 319:2.519
run_heat $ROUND  8  311:3.011 113:1.130 307:2.507 218:5.518
run_heat $ROUND  9  214:5.514 416:3.016 210:5.510 101:1.01
run_heat $ROUND 10  315:3.015 117:1.170 311:3.011 202:5.502
run_heat $ROUND 11  416:3.016 218:5.518 412:3.012 303:2.503
run_heat $ROUND 12  206:5.506 408:2.508 202:5.502 113:1.13
run_heat $ROUND 13  307:2.507 109:1.090 303:2.503 214:5.514
run_heat $ROUND 14  408:2.508 210:5.510 404:2.504 315:3.015
run_heat $ROUND 15  109:1.090 311:3.011 105:1.050 416:3.016
run_heat $ROUND 16  210:5.510 412:3.012 206:5.506 117:1.17
run_heat $ROUND 17  218:5.518 420:3.020 214:5.514 105:1.05
run_heat $ROUND 18  117:1.170 319:2.519 113:1.130 404:2.504
run_heat $ROUND 19  420:3.020 202:5.502 416:3.016 307:2.507
run_heat $ROUND 20  319:2.519 101:1.010 315:3.015 206:5.506 x


# Standings by times:
# 101 Jewell Jeansonne
# 105 Jerald Jerry    -- ineligible
# 109 Von Vassar
# 113 John Jefferys
# 117 forrest Figgins
# 303 Grant Gribble
# 404 Harrison Hanks
# 307 Lloyd Lightsey
# 408 Gary Grissom
# 319 Delmar Donnelly
# 311 Pasquale Procopio
# 412 Winford Weld
# 315 Demetrius Demming
# 416 Everette Esses
# 420 Stanton Salmon
# 202 Cedrick Charley
# 206 Ernest Edelman
# 210 Harold Hayek  -- ineligible
# 214 Thanh Turner
# 218 Sterling Spalla

user_login_coordinator

# One standings sheet per subgroup, with one row for each racer who is in it.
# Because Harold is ineligible, only 4 Tigers show up.
standings_sheet "All Racers / Bears" | jq -e 'length == 5' >/dev/null || test_fails Bears standings
standings_sheet "All Racers / Tigers" | jq -e 'length == 4' >/dev/null || test_fails Tigers standings
expect_row Pack 101 '[1,101,"Jewell Jeansonne","Lions",1,4,1.01,1.01,1.01]'
expect_row Pack 206 '[16,206,"Ernest Edelman","Tigers",2,4,5.506,5.506,5.506]'
expect_row Pack 214 '[17,214,"Thanh Turner","Tigers",3,4,5.514,5.514,5.514]'
expect_row Pack 218 '[18,218,"Sterling Spalla","Tigers",4,4,5.518,5.518,5.518]'
# Thanh came in 4th by time, but with Harold ineligible, he becomes 3rd, and Sterling is 4th
expect_places "All Racers / Tigers" Place 218 4

# Rank-specific awards:

curl_postj action.php "action=award.import&awardname=Most%20Lionic&awardtype=Design%20General&subgroup=Lions" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Most%20Tigerific&awardtype=Other&subgroup=Tigers" | check_jsuccess
curl_postj action.php "action=award.import&awardname=The%20Pack%20Design&awardtype=Design%20Trophy" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Bad%20Subgroup%20Name&awardtype=Design%20Trophy&subgroup=Typo" | check_jfailure

curl_postj action.php "action=award.winner&awardid=$(awardid_of "Most Lionic")&racerid=$(racerid_of 202)" | check_jfailure
curl_postj action.php "action=award.winner&awardid=$(awardid_of "Most Lionic")&racerid=$(racerid_of 101)" | check_jsuccess

curl_postj action.php "action=settings.write&n-rank-trophies=3" | check_jsuccess

curl_postj action.php "action=award.present&key=speed-3-$(classid_of "All Racers")-$(rankid_of Tigers)" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one '3rd Fastest in Tigers'
curl_getj "action.php?query=award.current" | expect_one Thanh

curl_postj action.php "action=award.present&key=speed-3-$(classid_of "All Racers")" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one '3rd Fastest in All Racers'
curl_getj "action.php?query=award.current" | expect_one Jefferys

curl_postj action.php "action=award.present&key=speed-3" | check_jsuccess
curl_getj "action.php?query=award.current" | expect_one '3rd Fastest in Pack'
curl_getj "action.php?query=award.current" | expect_one Jefferys
