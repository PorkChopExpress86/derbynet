#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator


RESET_SOURCE=balloting `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"
curl_postj action.php "action=racer.bulk&what=checkin&who=all" | check_jsuccess

curl_postj action.php "action=award.import&awardname=Best%20in%20Show&awardtype=Design%20Trophy" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Consolation%20Prize&awardtype=Design%20Trophy" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Sore%20Loser&awardtype=Design%20Trophy" | check_jsuccess

curl_postj action.php "action=award.import&awardname=Best%20Wolf&classname=White's%20Wolves&awardtype=Design%20Trophy" | check_jsuccess

curl_postj action.php "action=award.edit&awardid=$(awardid_of "Best in Show")&sort=1&ballot_depth=3" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Consolation Prize")&ballot_depth=3" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Consolation Prize")&ballot_depth=0" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Sore Loser")&sort=2&ballot_depth=2" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Best Wolf")&sort=3&ballot_depth=1" | check_jsuccess

BEST_IN_SHOW=$(awardid_of "Best in Show")
SORE_LOSER=$(awardid_of "Sore Loser")
BEST_WOLF=$(awardid_of "Best Wolf")

# A voter asks for the ballot (with the ballot password, which is empty) before
# casting votes
curl_getj "action.php?query=ballot.get&password=" | jq -e 'has("ballot")' >/dev/null || test_fails

# Voting not yet open
curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 212),$(racerid_of 136)]" | check_jfailure

curl_postj action.php "action=ballot.open&state=open" | check_jsuccess

curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 116),$(racerid_of 242)]" | check_jsuccess
# The same voter selecting different candidates doesn't add to the original choices
curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 313),$(racerid_of 237)]" | check_jsuccess
curl_postj action.php "action=vote.cast&awardid=$BEST_IN_SHOW&votes=[$(racerid_of 111),$(racerid_of 212)]" | check_jsuccess

curl_postj action.php "action=vote.cast&awardid=$BEST_WOLF&votes=[$(racerid_of 212),$(racerid_of 313)]" | check_jfailure # Too many choices

# Delete the session cookie in order to change voters
rm $COOKIES_CURL

curl_getj "action.php?query=ballot.get&password=" | jq -e 'has("ballot")' >/dev/null || test_fails
curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 504),$(racerid_of 237)]" | check_jsuccess

# Only the coordinator can see the results
user_login_coordinator

curl_getj "action.php?query=ballot.results" | \
    jq '.awards | .[].candidates | .[] | select(.score == 2).firstname' | \
    expect_eq '"Ian"'

curl_getj "action.php?query=ballot.results" | \
    jq '.awards | any( .candidates |  any( .carnumber == 136 ))' | \
    expect_eq false  # racer 36 (vote rejected)

curl_getj "action.php?query=ballot.results" | \
    jq '.awards | any( .candidates |  any( .carnumber == 242 ))' | \
    expect_eq false  # racer 42 (vote overwritten)

curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 136)]" | check_jsuccess
curl_getj "action.php?query=ballot.results" | \
    jq '.awards | any( .candidates |  any( .carnumber == 136 ))' | \
    expect_eq true

curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 242)]" | check_jsuccess
curl_getj "action.php?query=ballot.results" | \
    jq '.awards | any( .candidates |  any( .carnumber == 242 ))' | \
    expect_eq true
