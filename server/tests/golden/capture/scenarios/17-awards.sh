# The award, judging and presentation endpoints (#33): award.edit, award.import,
# award.winner with each refusal, award.adhoc, award.order, award.delete,
# award.xbs, award.present and award.current for judged and speed keys, and
# award.list with and without ad hoc awards.  Then the replies Django changes
# on purpose:
#
#   A1  an award made with no class_and_rank lists classid and rankid null in
#       PHP and 0 in Django, as every other pack-wide award does.
#   A2  a class_and_rank naming no class is stored by PHP; Django refuses it.
#   A3  award.present with neither key nor reveal never sets an outcome in
#       PHP; Django answers success.
#   A4  a full-field-calc naming no aggregate class is stored by PHP; Django
#       refuses it.
#   A5  a key longer than 100 characters is stored by PHP; Django refuses it.

use_session coord
reset_database
user_login_coordinator
use_session timer
user_login_timer
use_session coord
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
for i in 1 2 3; do
  post_ok "action=racer.import&firstname=Lion$i&lastname=Racer&carnumber=$((100 + i))&partition=Lions"
  post_ok "action=racer.import&firstname=Tiger$i&lastname=Racer&carnumber=$((200 + i))&partition=Tigers"
done
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=schedule.generate&roundid=1"
post_ok "action=schedule.generate&roundid=2"
post_ok "action=heat.select&roundid=1&now_racing=1"
race_round
post_ok "action=heat.select&roundid=2&now_racing=1"
race_round 5
LIONS=$(classid_of Lions)
TIGERS=$(classid_of Tigers)

capture_scenario awards/awards
capture "import" "POST action.php action=award.import&awardname=Best%20Paint&awardtype=Design%20Trophy"
capture "import for a class" "POST action.php action=award.import&awardname=Lion%20Paint&awardtype=Design%20Trophy&classname=lions&carnumber=101"
capture "import for a subgroup" "POST action.php action=award.import&awardname=Tiger%20Stripes&awardtype=Design%20General&subgroup=Tigers&racerid=$(racerid_of 202)&sort=9"
capture "import missing type" "POST action.php action=award.import&awardname=Nothing"
capture "import empty name" "POST action.php action=award.import&awardname=%20&awardtype=Other"
capture "import unknown type" "POST action.php action=award.import&awardname=X&awardtype=Design"
capture "import unknown class" "POST action.php action=award.import&awardname=X&awardtype=Other&classname=Bears"
capture "import unknown rank" "POST action.php action=award.import&awardname=X&awardtype=Other&subgroup=Bears"
capture "import wrong class" "POST action.php action=award.import&awardname=X&awardtype=Other&classname=Lions&subgroup=Tigers"
capture "import unknown car" "POST action.php action=award.import&awardname=X&awardtype=Other&carnumber=999"

capture "edit new" "POST action.php action=award.edit&awardid=new&name=Fastest%20Looking&awardtypeid=4&class_and_rank=$TIGERS,0"
capture "edit rename" "POST action.php action=award.edit&awardid=$(awardid_of "Best Paint")&name=Best%20Paint%20Job&sort=1"
capture "edit to a subgroup" "POST action.php action=award.edit&awardid=$(awardid_of "Fastest Looking")&class_and_rank=$TIGERS,$(rankid_of Tigers)"
capture "edit on the ballot" "POST action.php action=award.edit&awardid=$(awardid_of "Fastest Looking")&ballot_depth=2"

capture "winner" "POST action.php action=award.winner&awardid=$(awardid_of "Best Paint")&racerid=$(racerid_of 103)"
capture "winner no such award" "POST action.php action=award.winner&awardid=9999&racerid=$(racerid_of 103)"
capture "winner no such racer" "POST action.php action=award.winner&awardid=$(awardid_of "Best Paint")&racerid=9999"
capture "winner wrong class" "POST action.php action=award.winner&awardid=$(awardid_of "Lion Paint")&racerid=$(racerid_of 201)"
capture "winner of another class for a subgroup award" "POST action.php action=award.winner&awardid=$(awardid_of "Fastest Looking")&racerid=$(racerid_of 101)"
post_ok "action=racer.edit&racer=$(racerid_of 102)&firstname=Lion2&lastname=Racer&carno=102&carname=&rankid=$(rankid_of Lions)&exclude=1"
capture "winner excluded" "POST action.php action=award.winner&awardid=$(awardid_of "Best Paint")&racerid=$(racerid_of 102)"
capture "winner cleared" "POST action.php action=award.winner&awardid=$(awardid_of "Lion Paint")&racerid=0"

capture "adhoc" "POST action.php action=award.adhoc&racerid=$(racerid_of 203)&awardname=Most%20Glitter"
capture "adhoc replaced" "POST action.php action=award.adhoc&racerid=$(racerid_of 203)&awardname=Most%20Sparkle"
capture "xbs" "POST action.php action=award.xbs&racer=$(racerid_of 101)&value=1"
capture "list" "GET action.php?query=award.list"
capture "list without ad hoc" "GET action.php?query=award.list&adhoc=0"
capture "xbs off" "POST action.php action=award.xbs&racer=$(racerid_of 101)&value=0"
capture "order" "POST action.php action=award.order&awardid_1=$(awardid_of "Fastest Looking")&awardid_2=$(awardid_of "Best Paint")&awardid_3=$(awardid_of "Lion Paint")"
capture "delete" "POST action.php action=award.delete&awardid=$(awardid_of "Lion Paint")"

capture "present judged" "POST action.php action=award.present&key=award-$(awardid_of "Best Paint")"
capture "current judged" "GET action.php?query=award.current"
capture "present no winner" "POST action.php action=award.present&key=award-$(awardid_of "Fastest Looking")&reveal=1"
capture "current no winner" "GET action.php?query=award.current"
for key in speed-1 speed-2 speed-1-$LIONS speed-2-$TIGERS speed-1-$TIGERS-$(rankid_of Tigers) speed-9; do
  post_ok "action=award.present&key=$key&reveal=0"
  capture "current $key" "GET action.php?query=award.current"
done
post_ok "action=award.present&key=prize-1"
capture "current unrecognized" "GET action.php?query=award.current"
capture "calc-rule" "POST action.php action=award.calc-rule&full-field-calc=-1"
post_ok "action=award.present&key=speed-1"
capture "current speed-1 with no pack award" "GET action.php?query=award.current"
post_ok "action=award.calc-rule&full-field-calc=0"

post_ok "action=class.add&constituent_$LIONS=1&constituent_$TIGERS=1&name=Cubs"
post_ok "action=award.edit&awardid=new&name=Cub%20Spirit&awardtypeid=4&class_and_rank=$(classid_of Cubs),0"
capture "aggregate award list" "GET action.php?query=award.list&adhoc=0"
capture "A1 edit new with no class" "POST action.php action=award.edit&awardid=new&name=Plain&awardtypeid=4"
capture "A2 edit new in no class" "POST action.php action=award.edit&awardid=new&name=Ghost&awardtypeid=4&class_and_rank=9999,0"
capture "A3 present nothing" "POST action.php action=award.present"
capture "A4 calc-rule no aggregate" "POST action.php action=award.calc-rule&full-field-calc=9999"
capture "A5 present a long key" "POST action.php action=award.present&key=award-$(printf '1%.0s' $(seq 1 100))"
