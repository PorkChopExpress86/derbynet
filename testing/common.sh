#! /bin/bash
#
# Set environment variable PASSWORDS_FILE to point to a file containing, e.g.,
#
#RaceCoordinator:doyourbest
#RaceCrew:murphy
#
#

if [ "$BASE_URL" = "" ]; then
	echo Base URL required!
	exit 1
fi

OUTPUT_CURL="`dirname $0`/output.curl"
DEBUG_CURL="`dirname $0`/debug.curl"
COOKIES_CURL="`dirname $0`/cookies.curl"

# Colors are cosmetic.  tput fails when there's no terminal (e.g., TERM is unset
# under CI), and that must not fail the test.
function tput() {
    command tput "$@" 2>/dev/null || true
}

# macOS has md5, elsewhere it's md5sum.  Either way, just the hex digest.
function md5_digest() {
    if command -v md5 >/dev/null ; then
        md5
    else
        md5sum | cut -d' ' -f1
    fi
}

function header() {
    echo '###################### ' `caller 1 | cut -f3 -d\ ` ' #######################'
}
[ "$NOANNOUNCE" = "" ] && header

function stacktrace() {
    while caller $((n++)); do :; done;
}

function curl_get() {
	echo ' ' ' ' ' ' $1 >&2
	echo    >> $OUTPUT_CURL
	echo $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
        | sed -e 's/&nbsp;/ /g' | xmllint --format - | tee -a $OUTPUT_CURL
    #curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | sed -e 's/&nbsp;/ /g' | xmllint --format -
}

function curl_get_amper() {
	echo '     ' $1 >&2
	echo    >> $OUTPUT_CURL
	echo $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
		| grep -v '&' | xmllint --format - | tee -a $OUTPUT_CURL
}

# Fetch a page, but don't try to parse it as XML/HTML
function curl_text() {
	echo ' ' ' ' ' ' $1 >&2
	echo    >> $OUTPUT_CURL
	echo $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
        | sed -e 's/&nbsp;/ /g' | tee -a $OUTPUT_CURL
}

# Fetch a page and check that it is there and clean: HTTP status 200 after any
# redirects, and no PHP or Python error text in the body.  The body is not parsed,
# so a server is free to render HTML the way it likes.  Prints the body.
function curl_page() {
	echo ' ' ' ' ' ' $1 >&2
	echo    >> $OUTPUT_CURL
	echo $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	local STATUS
	STATUS=$(curl --location -s -o $DEBUG_CURL -w '%{http_code}' \
                  -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1)
	cat $DEBUG_CURL >> $OUTPUT_CURL
	if [ "$STATUS" != 200 ] ; then
		test_fails $1 answered with HTTP status $STATUS
	fi
	if grep -q 'Notice\|Warning\|Fatal\|Traceback' $DEBUG_CURL ; then
		test_fails $1 contains an error message: \
				   "$(grep -m 1 'Notice\|Warning\|Fatal\|Traceback' $DEBUG_CURL)"
	fi
	cat $DEBUG_CURL
}

# The visible text of an HTML page on stdin: scripts, styles and tags are gone,
# common entities are decoded, and runs of whitespace are one space.
function page_text() {
	perl -0777 -pe '
		s{<(script|style)\b.*?</\1>}{ }gsi;
		s{<[^>]*>}{ }gs;
		s{&nbsp;}{ }g; s{&lt;}{<}g; s{&gt;}{>}g; s{&quot;}{"}g;
		s{&#0*39;|&apos;}{\x27}g; s{&amp;}{&}g;
		s{\s+}{ }g;'
}

# Prints the HTTP status and the redirect target of a page, without following
# redirects, e.g. "302 http://localhost/login.php".
function curl_status() {
	curl -s -o /dev/null -w '%{http_code} %{redirect_url}' \
		 -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1
}

function curl_getj() {
	[ "$NOANNOUNCE" = "" ] && echo ' ' ' ' ' ' $1 >&2
	echo    >> $OUTPUT_CURL
	echo $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
        | tee -a $OUTPUT_CURL
}

function curl_post() {
	echo ' ' ' ' ' ' post $1 $2 >&2
	echo    >> $OUTPUT_CURL
	echo post $1 $2 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -d "$2" -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
		| xmllint --format - | tee -a $OUTPUT_CURL
}

function curl_postj() {
	[ "$NOANNOUNCE" = "" ] && echo ' ' ' ' ' ' post $1 $2 >&2
	echo    >> $OUTPUT_CURL
	echo post $1 $2 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL

    if [[ -n "$DBCHECK" ]] ; then
        if [[ ! "$2" =~ nodata ]] ; then
            curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL "$BASE_URL/action.php?query=database.check" \
                | tee $DEBUG_CURL | check_jsuccess
        fi
    fi

	curl --location -d "$2" -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/$1 | tee $DEBUG_CURL \
		| tee -a $OUTPUT_CURL
}

# curl_photo $1=url_tail $2=MD5digest
function curl_photo() {
    echo ' ' ' ' ' ' photo $1 expected to yield $2 >&2
    echo    >> $OUTPUT_CURL
    echo photo $1 $2 >> $OUTPUT_CURL
    echo    >> $OUTPUT_CURL
    OK=1
	PHOTO_MD5=`curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/photo.php/$1 | md5_digest`
    echo $PHOTO_MD5 | tee $DEBUG_CURL | tee -a $OUTPUT_CURL | grep -c $2 > /dev/null || OK=0
    if [ $OK -eq 0 ]; then
        test_fails Wrong photo result: $PHOTO_MD5
    fi
}

# curl_photo checks the MD5 digest of the photo that's returned, but for many photo manipulations,
# the digest depends on the exact PHP and library versions used.  curl_photo_any just tests that
# we get some kind of picture and not a failure.
#
# curl_photo_any $1=url_tail
function curl_photo_any() {
    echo ' ' ' ' ' ' photo_any $1 >&2
    echo    >> $OUTPUT_CURL
    echo photo $1 >> $OUTPUT_CURL
    echo    >> $OUTPUT_CURL

    COUNT=`curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/photo.php/$1 | wc -c`
    if [ $COUNT -lt 1000 ]; then
        test_fails No photo result for $1
    fi
}

function curl_put_snapshot() {
    echo ' ' ' ' ' ' snapshot.put $1 >&2
    echo    >> $OUTPUT_CURL
    echo snapshot.put $1 >> $OUTPUT_CURL
    echo    >> $OUTPUT_CURL
    curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
        -X POST -F snapshot="@$1" -F action=snapshot.put | tee $DEBUG_CURL | \
    xmllint --format - | tee -a $OUTPUT_CURL
}

function user_login() {
	# $1 = user name
    PWD=$(sed -n -e "s/^$1://p" "${PASSWORDS_FILE:-`dirname $0`/default.passwords}")
	echo ' ' ' ' ' ' login $1 >&2
	echo    >> $OUTPUT_CURL
	echo login $1 >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
	curl --location -d "action=role.login&name=$1&password=$PWD" \
        -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php | tee $DEBUG_CURL \
		| tee -a $OUTPUT_CURL | check_jsuccess
}

function user_login_coordinator() {
    user_login RaceCoordinator
}

function user_login_crew() {
    user_login RaceCrew
}

function user_login_timer() {
    user_login Timer
}

function user_login_photo() {
    user_login Photo
}

function user_logout() {
	curl_postj action.php "action=role.login" | check_jsuccess logout
}

function test_fails() {
    tput setaf 1  # red text
	echo TEST FAILURE: $*
    stacktrace
	echo RECEIVED:
	head -n 100 $DEBUG_CURL
    tput setaf 0  # black text
    exit 1
}

function check_success() {
	# Expecting stdin
    OK=1
	grep -c "<success[ />]" > /dev/null || OK=0 
	if [ $OK -eq 0 ]; then
        test_fails
	fi
}

function check_jsuccess() {
	# Expecting stdin
    OK=1
    jq -s -e ' length > 0 and (map(.outcome.summary == "success") | all)' >/dev/null || OK=0
	if [ $OK -eq 0 ]; then
        echo Tail failed
        test_fails
	fi
}

# Some actions are expected to fail, i.e., produce a 
# <failure ...>...</failure>
# response.  The test fails if that's not the case.
function check_failure() {
	# Expecting stdin
    OK=0
	grep -c "<failure[ />]" > /dev/null && OK=1
	if [ $OK -eq 0 ]; then
        test_fails EXPECTING ACTION TO FAIL
	fi
}

function check_jfailure() {
	# Expecting stdin
    OK=0
    jq -e '.outcome.summary == "failure"' >/dev/null && OK=1
	if [ $OK -eq 0 ]; then
        test_fails EXPECTING ACTION TO FAIL
	fi
}

function expect_count {
	# Expecting stdin
	if [ "`grep -c "$1"`" -ne $2 ]; then
        test_fails expecting $2 occurrences of $1
	fi
}

function expect_one {
	if [ "`grep -c "$1"`" -ne 1 ]; then
        test_fails expecting an occurrence of $1
	fi
}

# Reads page text (see page_text) on stdin and fails unless the sentence $1
# appears in it exactly once, as whole words.
function expect_sentence {
    local N
    N=$(grep -o -E "(^| )$(printf '%s' "$1" | sed -e 's#[][\.*^$/+?(){}|]#\\&#g')( |\$)" | wc -l)
    if [ "$N" -ne 1 ]; then
        test_fails expecting one occurrence of the sentence "$1", but found $N
    fi
}

function expect_eq {
    if [ "`cat`" != "$1" ]; then
        test_fails expecting string "$1"
    fi
}


# For generating data:
# select roundid, heat,
# carnumber || ':' || (1 + abs(random()) % 3) || '.' ||
#     (abs(random()) % 10) || (abs(random()) % 10) || (abs(random()) % 10) as L1,
# (select carnumber from RaceChart join RegistrationInfo using (racerid)
#     where lane = 2 and roundid = lane1.roundid and heat = Lane1.heat)
#  || ':' || (1 + abs(random()) % 3) || '.' ||
#     (abs(random()) % 10) || (abs(random()) % 10) || (abs(random()) % 10) as L2,
# (select carnumber from RaceChart join RegistrationInfo using (racerid)
#     where lane = 3 and roundid = lane1.roundid and heat = Lane1.heat)
#  || ':' || (1 + abs(random()) % 3) || '.' ||
#     (abs(random()) % 10) || (abs(random()) % 10) || (abs(random()) % 10) as L3,
# (select carnumber from RaceChart join RegistrationInfo using (racerid)
#     where lane = 4 and roundid = lane1.roundid and heat = Lane1.heat)
#  || ':' || (1 + abs(random()) % 3) || '.' ||
#     (abs(random()) % 10) || (abs(random()) % 10) || (abs(random()) % 10) as L4
# from RaceChart lane1 join RegistrationInfo using (racerid)
# where finishtime is null and lane = 1 and roundid = 2 order by heat;

# Confirm what roundid/heat is current and simulate timer interaction for running one heat
# Usage: run_heat <roundid> <heat> <lane1> <lane2> <lane3> <lane4> ?<skip-check_heat_ready>
#  where <laneN> is either just a time, a carnumber plus a time, e.g.,
#  101:3.210, or a hypen for a bye.
function run_heat() {
    SKIP_CHECK_HEAT_READY=0

    if [[ "$1" == "-place" ]] ; then
        shift
        KEY=place
    else
        KEY=lane
    fi
    
    ROUNDID=$1
    HEAT=$2
    shift 2

    curl_getj "action.php?query=poll.coordinator" | \
        jq -e ".[\"current-heat\"] | .[\"now_racing\"] == true and
               .roundid == $ROUNDID and .heat == $HEAT" >/dev/null || \
                   test_fails Wrong heat or not racing

    CARNOS=`jq -c '.racers | map(.carnumber)' $DEBUG_CURL`

    
    SUGGEST_UPDATE=
    # SUGGEST_UPDATE=1
    if [[ "$1" == *:* ]] ; then
        SUGGEST_UPDATE=
    fi

    if [ $SUGGEST_UPDATE ] ; then
        echo `caller 0 | cut -d\  -f3`:`caller 0 | cut -d\  -f1`
        echo -n run_heat $ROUNDID $HEAT ""
    fi

    FINISHED=""
    LANE=1
    BYES=0
    while [ $# -gt 0 ]; do
        if [ "$1" = "x" ] ; then
            SKIP_CHECK_HEAT_READY=1
        elif [[ "$1" == "-" ]] ; then
            # Lane not occupied.  CARNOS won't have a blank, so adjust BYES
            let BYES+=1
            let LANE+=1
            if [ $SUGGEST_UPDATE ] ; then echo -n " -" ; fi
        elif [[ "$1" == *:* ]] ; then
            EXPECTED_CARNO=$(echo "$1" | cut -d: -f1)
            TIME=$(echo "$1" | cut -d: -f2)

            ACTUAL_CARNO=$(echo $CARNOS | jq -j ".[$LANE-$BYES-1]")

            if [[ "$EXPECTED_CARNO" != "$ACTUAL_CARNO" ]] ; then
                test_fails Wrong car number in lane $LANE: \
                           expected $EXPECTED_CARNO instead of $ACTUAL_CARNO
            fi

            FINISHED="$FINISHED&$KEY$LANE=$TIME"
            let LANE+=1
        else
            FINISHED="$FINISHED&lane$LANE=$1"
            if [ $SUGGEST_UPDATE ] ; then echo -n " $(echo $CARNOS | jq -j ".[$LANE-$BYES-1]"):$1" ; fi
            let LANE+=1
        fi
        shift
    done
    if [ $SUGGEST_UPDATE ] ; then echo ; fi

    curl_post action.php "action=timer-message&message=STARTED" | check_success
    curl_post action.php "action=timer-message&message=FINISHED$FINISHED" | check_success
    if [ $SKIP_CHECK_HEAT_READY -eq 0 ] ; then
        cat $DEBUG_CURL | expect_one "<heat-ready[ />]"
    fi
}

# Usage: staged_heat <lane1-carno> <lane2-carno> <lane3-carno> <lane4-carno>
#  "Bye" lanes are given as 0's
# Usage: staged_heat <lane1-carno> <lane2-carno> <lane3-carno> <lane4-carno> <lane5-carno> <lane6-carno>
function staged_heat6() {
    curl_getj "action.php?query=poll.coordinator" | \
        jq -e ".racers | \
            ($1 == 0 or map(select ( .lane == 1 ))[0].carnumber == $1) and \
            ($2 == 0 or map(select ( .lane == 2 ))[0].carnumber == $2) and \
            ($3 == 0 or map(select ( .lane == 3 ))[0].carnumber == $3) and \
            ($4 == 0 or map(select ( .lane == 4 ))[0].carnumber == $4) and \
            ($5 == 0 or map(select ( .lane == 5 ))[0].carnumber == $5) and \
            ($6 == 0 or map(select ( .lane == 6 ))[0].carnumber == $6)" >/dev/null || \
        test_fails
}

# Lookups.  They read one query and leave $DEBUG_CURL and $OUTPUT_CURL alone, so
# a caller can use them in the middle of a command that reads the previous
# response.  A lookup that finds nothing stops the whole test.  The suite never
# assumes a database ID: it asks for the ID by its stable key (car number, class
# name, award name) or takes it from the response that created the thing.
function lookup_get() {
    curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL "$BASE_URL/$1"
}

# $$ is the test script's pid even inside $(...), where a plain "exit" would
# only leave the subshell.
function lookup_fails() {
    tput setaf 1  # red text
    echo TEST FAILURE: $* >&2
    stacktrace >&2
    tput setaf 0  # black text
    kill -TERM $$
    exit 1
}

# racerid_of <carnumber>
racerid_of() {
    local ID
    ID=$(lookup_get "action.php?query=racer.list" \
             | jq --arg c "$1" '.racers | map(select(.carnumber | tostring == $c))[0].racerid // empty')
    [ -n "$ID" ] || lookup_fails "No racer has car number $1"
    echo $ID
}

# roundid_of <class name or prefix of one> <round number>
roundid_of() {
    local ID
    ID=$(lookup_get "action.php?query=poll&values=rounds" \
             | jq --arg c "$1" --argjson r "$2" \
                  '.rounds | map(select(.round == $r)) |
                   (map(select(.class == $c))[0] // map(select(.class | startswith($c)))[0]).roundid // empty')
    [ -n "$ID" ] || lookup_fails "No round $2 of a class named $1"
    echo $ID
}

# awardid_of <award name or prefix of one>
awardid_of() {
    local ID
    ID=$(lookup_get "action.php?query=award.list" \
             | jq --arg n "$1" \
                  '.awards | (map(select(.awardname == $n))[0] // map(select(.awardname | startswith($n)))[0]).awardid // empty')
    [ -n "$ID" ] || lookup_fails "No award is named $1"
    echo $ID
}

# These three print "null", not a failure, when there is no match, because some
# tests check that a group is gone.
#
# "$1" is a (unique) prefix for the rank name sought
# E.g. WOLF_RANK=$(rankid_of "White")
rankid_of() {
    lookup_get "action.php?query=class.list" \
        | jq --arg n "$1" '.classes | map(.subgroups) | add | map(select(.name | startswith($n)))[0].rankid'
}
classid_of() {
    lookup_get "action.php?query=class.list" \
        | jq --arg n "$1" '.classes | map(select(.name | startswith($n)))[0].classid'
}
partitionid_of() {
    lookup_get "action.php?query=poll&values=partitions" \
        | jq --arg n "$1" '.partitions | map(select(.name | startswith($n)))[0].partitionid'
}

# standings_sheet <name>: the rows of the standings sheet "Standings for <name>"
# in the workbook that export-json.php serves, as a JSON array of objects keyed
# by column header.  The sheet's first row names it and its third row holds the
# headers, so the sheet's own title is not needed, and neither is any ID.
standings_sheet() {
    curl_getj export-json.php | \
        jq -c --arg t "Standings for $1" \
           'map(select(.[1][0][0]? == $t))[0][1] as $rows
            | $rows[2] as $h
            | $rows[3:] | map([$h, .] | transpose | map({(.[0]): .[1]}) | add)'
}

# standings_row <name> <carnumber>: one row of standings_sheet <name>
standings_row() {
    standings_sheet "$1" | \
        jq -c --arg c "$2" 'map(select(.["Car Number"] | tostring == $c))[0] // empty'
}

# expect_row <standings name> <carnumber> <the whole row, as a JSON array>
# Types are not the fact under test, so values are compared as strings.
expect_row() {
    standings_row "$1" $2 | \
        jq -e --argjson e "$3" '[.[]] | map(tostring) == ($e | map(tostring))' >/dev/null || \
        test_fails Standings for "$1", car $2 should be "$3"
}

# expect_places <standings name> <column header> <carnumber> <place> ...
expect_places() {
    local SHEET="$1" COLUMN="$2"
    shift 2
    while [ $# -gt 0 ] ; do
        standings_row "$SHEET" $1 | jq -e --arg c "$COLUMN" --arg p $2 '.[$c] | tostring == $p' >/dev/null || \
            test_fails Standings for "$SHEET", car $1 should have $COLUMN = $2
        shift 2
    done
}

# Reads a roster.new response on stdin and prints the finalists' car numbers as
# a sorted JSON array, e.g. [101,111,126].
finalists_carnumbers() {
    local IDS
    IDS=$(jq -c '[.finalists[].racerid]')
    lookup_get "action.php?query=racer.list" \
        | jq -c --argjson ids "$IDS" \
             '[.racers[] | select(.racerid as $r | $ids | index($r)) | .carnumber] | sort'
}
