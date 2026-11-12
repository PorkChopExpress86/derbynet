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

if [ "$DERBYNET_BACKEND" = django ] ; then
    # D86: on Django a racer's voting token is the voter, in place of the
    # ballot password and the session.  The coordinator reads two racers'
    # tokens from the check-in page's Voting code sheet, and each voter
    # names its token to ballot.get as voting_token; later calls in the
    # session use it.
    function voting_token_of() {
        curl_text "checkin/racer/$(racerid_of $1)/voting-code/" | \
            sed -n 's|.*data-voting-url>[^<]*/vote/code/\([0-9a-f-]*\)<.*|\1|p'
    }
    TOKEN_101=$(voting_token_of 101)
    TOKEN_161=$(voting_token_of 161)
    [ -n "$TOKEN_101" ] && [ -n "$TOKEN_161" ] && [ "$TOKEN_101" != "$TOKEN_161" ] || \
        test_fails no voting tokens on the Voting code sheet

    function refused_for_no_token() {
        jq -e '.outcome.summary == "failure" and .outcome.code == "no-token"' >/dev/null || \
            test_fails expecting a no-token refusal
    }

    # No token, no ballot
    curl_getj "action.php?query=ballot.get" | refused_for_no_token

    # The first voter asks for the ballot with its token before casting votes
    curl_getj "action.php?query=ballot.get&voting_token=$TOKEN_101" | jq -e 'has("ballot")' >/dev/null || test_fails
else
# A voter asks for the ballot (with the ballot password, which is empty) before
# casting votes
curl_getj "action.php?query=ballot.get&password=" | jq -e 'has("ballot")' >/dev/null || test_fails
fi

# Voting not yet open
curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 212),$(racerid_of 136)]" | check_jfailure

curl_postj action.php "action=ballot.open&state=open" | check_jsuccess

curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 116),$(racerid_of 242)]" | check_jsuccess
# The same voter selecting different candidates doesn't add to the original choices
curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[$(racerid_of 313),$(racerid_of 237)]" | check_jsuccess
curl_postj action.php "action=vote.cast&awardid=$BEST_IN_SHOW&votes=[$(racerid_of 111),$(racerid_of 212)]" | check_jsuccess

curl_postj action.php "action=vote.cast&awardid=$BEST_WOLF&votes=[$(racerid_of 212),$(racerid_of 313)]" | check_jfailure # Too many choices

if [ "$DERBYNET_BACKEND" = django ] ; then
    # D86: another phone with the first voter's token edits the same ballot,
    # so it sees that voter's choices.  A cookie jar of its own is the
    # other phone.
    FIRST_CHOICES="[$(racerid_of 313),$(racerid_of 237)]"
    (
        COOKIES_CURL=$(mktemp)
        curl_getj "action.php?query=ballot.get&voting_token=$TOKEN_101" | \
            jq -c ".ballot[\"$SORE_LOSER\"].votes" | expect_eq "$FIRST_CHOICES"
        rm -f $COOKIES_CURL
    )
    # A token that is no racer's is refused, and leaves the session with none
    curl_getj "action.php?query=ballot.get&voting_token=not-a-token" | refused_for_no_token
    curl_postj action.php "action=vote.cast&awardid=$SORE_LOSER&votes=[]" | refused_for_no_token

    # The second voter's token, in place of a new session
    curl_getj "action.php?query=ballot.get&voting_token=$TOKEN_161" | jq -e 'has("ballot")' >/dev/null || test_fails
else
# Delete the session cookie in order to change voters
rm $COOKIES_CURL

curl_getj "action.php?query=ballot.get&password=" | jq -e 'has("ballot")' >/dev/null || test_fails
fi
# Car 504 is Denny Deering, imported as 519: import-roster.sh renumbers the
# Webelos from 501 in the order they were imported.
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
