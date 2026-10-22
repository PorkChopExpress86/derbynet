#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

curl_postj action.php "action=racer.pass&racer=$(racerid_of 101)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 303)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 405)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 207)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 502)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 111)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 313)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 415)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 217)&value=1" | check_jsuccess
#curl_postj action.php "action=racer.pass&racer=19&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 121)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 323)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 425)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 227)&value=1" | check_jsuccess
#curl_postj action.php "action=racer.pass&racer=29&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 131)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 333)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 435)&value=1" | check_jsuccess
##curl_postj action.php "action=racer.pass&racer=37&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&barcode=PWD237&value=1" | check_jsuccess
##curl_postj action.php "action=racer.pass&racer=39&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&barcode=PWD508&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 141)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 343)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 445)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 247)&value=1" | check_jsuccess
curl_postj action.php "action=racer.pass&racer=$(racerid_of 510)&value=1" | check_jsuccess

# Exclude car 111
curl_postj action.php "action=racer.edit&racer=$(racerid_of 111)&firstname=Carroll&lastname=Cybulski&carno=111&carname=Vroom&rankid=$(rankid_of Lions)&exclude=1" | check_jsuccess

# Change a racer's den
curl_postj action.php "action=racer.edit&racer=$(racerid_of 508)&firstname=Jeffress&lastname=Jamison&carno=139&carname=&rankid=$(rankid_of Bears)" | check_jsuccess

# Reject partitionid -1
curl_postj action.php "action=racer.edit&racer=$(racerid_of 445)&firstname=Savvy&lastname=Savant&carno=445&carname=&note_from=&partitionid=-1&exclude=0" | check_jfailure

# TODO: There was a bug that the new-row returned from this was basically empty; this doesn't test that
#
# After changing a den, adding a new racer used to be a bug (extra Roster row).
# The effect was observable extra entries for heats that included the
# den-changed racer.
curl_postj action.php "action=racer.add&firstname=Fred&lastname=Flintstone&carno=789&carname=&partitionid=$(partitionid_of White)&note_from=Bedrock" | check_jsuccess
curl_getj "action.php?query=racer.list" | \
    jq -e '.racers | map(select(.carnumber == 789))[0].note_from == "Bedrock"' > /dev/null || test_fails
