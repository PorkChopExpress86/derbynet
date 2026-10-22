#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

## Testing the "add new round" functionality.  We're just generating the
## rosters, not making schedules or doing any racing.

# We expect there are no rounds beyond a first round for each of the classes.  A
# round that doesn't exist can't be deleted.
curl_postj action.php "action=roster.delete&roundid=$(unused_roundid)" | check_jfailure

# Top 3 from the first round of Lions & Tigers
curl_postj action.php "action=roster.new&roundid=$(roundid_of Lions 1)&top=3" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | jq -e '. == [101,131,141] or . == [101,106,136]' >/dev/null || test_fails
ROUNDID=`jq '.["new-round"].roundid' $DEBUG_CURL`

[ "$ROUNDID" ] || test_fails

# The new round is now deletable
curl_postj action.php "action=roster.delete&roundid=$ROUNDID" | check_jsuccess
# It's now gone, so second deletion fails
curl_postj action.php "action=roster.delete&roundid=$ROUNDID" | check_jfailure

# Top 3 from each rank in the first round of White's Wolves
curl_postj action.php "action=roster.new&roundid=$(roundid_of White 1)&top=3&bucketed=1" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | jq -e '. == [207,227,247] or . == [232,237,242]' >/dev/null || test_fails

# Grand Finals round, 4 from each den
# In test-basic-checkins, Bears & Freres only have 2 racers, so it's a total of 14 racers
# In test-master-schedule, Bears & Freres have 2 racers, and Webelos only 3, so it's a total of 13 finalists
curl_postj action.php "action=roster.new&roundid=&top=4&bucketed=1&classid_$(classid_of Lions)=1&classid_$(classid_of White)=1&classid_$(classid_of Bears)=1&classid_$(classid_of Webelos)=1&classname=Grand%20Finals" \
 | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | jq -e '. == [101,121,131,139,141,207,227,237,247,303,343,502,510,999] or . == [101,106,136,166,212,232,237,242,303,308,508,509,510]' >/dev/null || test_fails

ROUNDID=$(jq '.["new-round"].roundid' $DEBUG_CURL)

curl_postj action.php "action=roster.delete&roundid=$ROUNDID" | check_jsuccess

# Grand Finals round, top 5 overall
curl_postj action.php "action=roster.new&roundid=&top=5&classid_$(classid_of Lions)=1&classid_$(classid_of White)=1&classid_$(classid_of Bears)=1&classid_$(classid_of Webelos)=1&classname=Grand%20Finals-2" | check_jsuccess
finalists_carnumbers < $DEBUG_CURL | jq -e '. == [139,207,227,303,510] or . == [101,106,136,166,242]' >/dev/null || test_fails

ROUNDID=$(jq '.["new-round"].roundid' $DEBUG_CURL)
curl_postj action.php "action=roster.delete&roundid=$ROUNDID" | check_jsuccess

