# The racer, check-in and import endpoints (#29): racer.list in all five
# orders, racer.pass by each barcode form, walk-in racers made by racer.pass
# and by racer.list, racer.add, racer.edit, racer.delete and racer.bulk.  Then
# two rosters of their own for the fixes Django makes on purpose:
#
#   D34  racer.import names partition LIONS beside Lions.  PHP makes a second
#        partition; Django joins Lions.
#   C14  a PWDwi walk-in with car-numbering multiplier 0.  PHP assigns
#        $partition, not $partitionid, so the walk-in falls to Default; Django
#        puts it in the last partition.
#
# Every racer gets a distinct check-in time before an order=checkin list, so no
# two rows tie on seq.

use_session coord
reset_database
user_login_coordinator
post_ok "action=racer.import&firstname=Adolfo&lastname=Asher&partition=Lions&carnumber=101"
post_ok "action=racer.import&firstname=Ben&lastname=Bittinger&partition=Lions&carnumber=102"
post_ok "action=racer.import&firstname=Antoine&lastname=Akiyama&partition=Tigers&carnumber=201"
post_ok "action=racer.import&firstname=Diego&lastname=de la Cruz&partition=Tigers&carnumber=202"
post_ok "action=racer.import&firstname=Freeman&lastname=Fizer&partition=Bears&carnumber=301"
post_ok "action=racer.import&firstname=Twin&lastname=One&partition=Bears&carnumber=302"
post_ok "action=racer.import&firstname=Twin&lastname=Two&partition=Bears&carnumber=302"

# Walk-in racers' last names are numbers, which racerid_named cannot read.
DIEGO=$(racerid_named "Diego de la Cruz")
TWIN1=$(racerid_named "Twin One")
TWIN2=$(racerid_named "Twin Two")

capture_scenario racers/check-in
for order in name class rank car; do
  capture "list order=$order" "GET action.php?query=racer.list&order=$order"
done
capture "pass by racer" "POST action.php action=racer.pass&racer=$(racerid_of 101)&value=1"
capture "pass PWDid" "POST action.php action=racer.pass&barcode=PWDid$(racerid_of 201)&value=1"
capture "pass PWD carno" "POST action.php action=racer.pass&barcode=PWD102"
capture "pass PWD shared carno" "POST action.php action=racer.pass&barcode=PWD302&value=1"
capture "pass PWD unknown carno" "POST action.php action=racer.pass&barcode=PWD999&value=1"
capture "pass PWDwi existing car" "POST action.php action=racer.pass&barcode=PWDwi301&value=1"
capture "pass PWDwi new car" "POST action.php action=racer.pass&barcode=PWDwi250&value=1"
capture "list PWDwi new car" "GET action.php?query=racer.list&barcode=PWDwi310"
capture "list PWD carno" "GET action.php?query=racer.list&barcode=PWD202"
capture "list PWDid" "GET action.php?query=racer.list&barcode=PWDid$(racerid_of 201)"
post_ok "action=racer.pass&racer=$DIEGO&value=1"
post_ok "action=racer.pass&racer=$TWIN1&value=1"
post_ok "action=racer.pass&racer=$TWIN2&value=1"
post_ok "action=racer.pass&barcode=PWD310&value=1"
capture "list order=checkin" "GET action.php?query=racer.list&order=checkin"
capture "revert by racer" "POST action.php action=racer.pass&racer=$(racerid_of 101)&value=0"
capture "list order=checkin after revert" "GET action.php?query=racer.list&order=checkin"

use_session photo
user_login_photo
capture "photo role pass PWD carno" "POST action.php action=racer.pass&barcode=PWD201&value=1"
use_session coord

capture "add" "POST action.php action=racer.add&firstname=Fred&lastname=Flintstone&carno=&carname=Rock&partitionid=$(partitionid_of Lions)&note_from=Bedrock"
FRED=$(racerid_of 103)
capture "add with no partition" "POST action.php action=racer.add&firstname=Wilma&lastname=Flintstone&carno=&carname=&note_from="
WILMA=$(racerid_of 401)
capture "edit duplicate car" "POST action.php action=racer.edit&racer=$FRED&firstname=Fred&lastname=Flintstone&carno=101&carname=Rock"
capture "edit partition -1" "POST action.php action=racer.edit&racer=$FRED&partitionid=-1"
capture "edit partition" "POST action.php action=racer.edit&racer=$FRED&partitionid=$(partitionid_of Bears)&carno=303"
capture "delete" "POST action.php action=racer.delete&racer=$WILMA"
capture "bulk number" "POST action.php action=racer.bulk&what=number&who=c$(classid_of Bears)&start=501&renumber=1"
capture "bulk unknown who" "POST action.php action=racer.bulk&what=checkin&who=x"
capture "bulk eligibility" "POST action.php action=racer.bulk&what=eligibility&who=d$(partitionid_of Tigers)&value=0"
capture "list order=car at the end" "GET action.php?query=racer.list&order=car"

# ----- D34
reset_database
user_login_coordinator
post_ok "action=racer.import&firstname=Adolfo&lastname=Asher&partition=Lions&carnumber=101"
capture_scenario racers/import-partition-case
capture "import LIONS" "POST action.php action=racer.import&firstname=Leo&lastname=Upper&partition=LIONS"
capture "list after LIONS" "GET action.php?query=racer.list&order=car"

# ----- C14
reset_database
user_login_coordinator
post_ok "action=settings.write&car-numbering=0%2B1"
post_ok "action=racer.import&firstname=Adolfo&lastname=Asher&partition=Lions"
post_ok "action=racer.import&firstname=Antoine&lastname=Akiyama&partition=Tigers"
capture_scenario racers/walk-in-multiplier-0
capture "pass PWDwi new car" "POST action.php action=racer.pass&barcode=PWDwi7&value=1"
capture "list after walk-in" "GET action.php?query=racer.list&order=car"

# ----- Requests Django answers differently on purpose (#29), each listed in
# deviations.json: an unknown racer id in racer.edit and racer.pass, an
# anonymous racer list and check-in (D5, S23), and a car number that is not a
# whole number, last so no later list shows it.
reset_database
user_login_coordinator
post_ok "action=racer.import&firstname=Adolfo&lastname=Asher&partition=Lions&carnumber=101"
capture_scenario racers/refusals
capture "edit unknown racer" "POST action.php action=racer.edit&racer=9999&firstname=Nobody"
capture "pass unknown racer" "POST action.php action=racer.pass&racer=9999&value=1"
use_session anonymous
capture "anonymous list" "GET action.php?query=racer.list"
capture "anonymous pass PWD carno" "POST action.php action=racer.pass&barcode=PWD101&value=1"
use_session coord
capture "import text car number" "POST action.php action=racer.import&firstname=Text&lastname=Car&partition=Lions&carnumber=A12"
