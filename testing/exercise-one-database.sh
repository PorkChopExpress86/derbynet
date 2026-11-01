#! /bin/bash

# exercise-one-database.sh <base_url> sqlite [optional path to file]

BASE_URL=$1
shift
DBTYPE=$1
shift

set -e -E -o pipefail
source `dirname $0`/common.sh

prepare_for_setup() {
    # TODO: Simulate new database configuration by doing:
    # Delete /Library/WebServer/Documents/xsite/local/config-database.inc
    #  and /Library/WebServer/Documents/xsite/local/config-roles.inc
    # and then delete the cookies file
    #
    #user_login_coordinator
    # But it's OK if the login fails owing to there being no config file.
    PWD=$(sed -n -e "s/^RaceCoordinator://p" "${PASSWORDS_FILE:-`dirname $0`/default.passwords}")
	echo    >> $OUTPUT_CURL
	echo login RaceCoordinator >> $OUTPUT_CURL
	echo    >> $OUTPUT_CURL
    SUCCESSFUL=1
	curl --location -d "action=role.login&name=RaceCoordinator&password=$PWD" \
        -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php | tee $DEBUG_CURL \
		| tee -a $OUTPUT_CURL | \
        jq -s -e ' length > 0 and (map(.outcome.summary == "success") | all)' >/dev/null || SUCCESSFUL=0
    if [ ! $SUCCESSFUL ] ; then
        tput setaf 1  # red text
        echo Initial login unsuccessful
    fi
    tput setaf 0  # black text

    # TODO Summarize success or failure here
}

# A new database named testdb, set up as a first-time user does.
ez_setup() {
    prepare_for_setup
    curl_postj action.php "action=setup.nodata&ez-new=testdb" > /dev/null
}

function user_login_coordinator() {
    user_login RaceCoordinator
}

# $1 is the name of the .js file in puppeteer subdirectory
puppeteer_test() {
    echo
    echo " " " " " " $1
    node "`dirname $0`/puppeteer/$1" "$BASE_URL"
}

# The snapshot round trip at the end of run_tests, as steps.
snapshot_get() {
    # test-balloting.sh deleted the cookie jar, and a snapshot is for the coordinator
    user_login_coordinator
    echo Taking snapshot in $SNAPSHOT
    curl_get_xml "action.php?query=snapshot.get" > $SNAPSHOT
}

snapshot_put_as_nobody() {
    user_logout
    curl_put_snapshot $SNAPSHOT | check_failure
}

snapshot_put() {
    user_login_coordinator
    curl_put_snapshot $SNAPSHOT | check_success
}

source `dirname $0`/steps.sh

run_tests() {
############################## Basic Racing ##############################
    RESET_SOURCE=ex0 step reset-database.sh
    step import-roster.sh
    step test-den-changes.sh

    step photo-setup.sh
    step test-photo-upload.sh

    step test-each-role.sh
    step test-permissions.sh

    step test-basic-racing.sh

    step puppeteer_test all-pages-test.js
    step puppeteer_test coordinator-test.js
    step puppeteer_test ondeck-columns-test.js

    step test-awards.sh basic
    step test-new-rounds.sh

    step test-each-role.sh
    step test-scenes.sh

    step test-photo-manipulations.sh
    step test-photo-assignments.sh basic
    step test-slide-next.sh

    step test-racer-query.sh

    # Resets database
    step --resets test-car-numbers.sh
    # Doesn't reset the database, so it can't be retried on its own
    step test-extended-scheduling.sh

    # Resets database
    step --resets test-reschedule.sh
    # Resets database
    step --resets test-rotation-schedule.sh

############################## Standings by Rank ##############################
    # Resets database
    step --resets test-standing-by-rank.sh
    step test-each-role.sh
    # Resets database
    step --resets test-subgroup-ntrophies.sh

############################## Points Racing ##############################
    # Resets database
    step --resets test-points-racing.sh

############################## Rounds Playlist ##############################
    # Resets database
    step --resets test-playlist.sh

############################## One-Run-Per-Car Racing ##############################
    step test-model-a-club.sh

    # Resets database
    step --resets test-messaging.sh

############################## Racing Groups and Partitions ##############################
    # checkin-empty-test.js reads the partition's ID from the page (#div-1), and
    # the Puppeteer tests are outside the shifted-ID run, so this reset doesn't shift
    DERBYNET_SHIFT_IDS= RESET_SOURCE=ex1 step reset-database.sh
    step puppeteer_test checkin-empty-test.js

    # Resets database
    step --resets test-partitions.sh
    step test-racing-groups.sh
    # Resets database
    step --resets test-preferences.sh

############################## Master Schedule ##############################
    RESET_SOURCE=ex2 step reset-database.sh
    step import-roster.sh
    step test-den-changes.sh
    step test-master-schedule.sh

    step test-awards.sh master
    step test-new-rounds.sh
    step test-each-role.sh
    step test-photo-manipulations.sh
    step test-photo-assignments.sh master
    step test-photo-upload.sh
    step test-each-role.sh

    # Resets database
    step --resets test-aggregate-rounds.sh
    # Resets database
    step --resets test-aggregate-classes.sh
    # Resets database
    step --resets test-aggregate-nonracing.sh

############################## Snapshot Export and Import ##############################
    RESET_SOURCE=ex3 step reset-database.sh
    step import-roster.sh
    step test-den-changes.sh
    step test-unused-lanes.sh

    # Resets database.  Balloting moved to the backlog (#54, D7), and the Django
    # server doesn't serve it yet.
    if [ "$DERBYNET_BACKEND" = django ] ; then
        skip_step test-balloting.sh "balloting is deferred to #54 (D7)"
    else
        step --resets test-balloting.sh
    fi

    if [ -n "$PARITY_DIR" ] ; then
        # A fixed name, so that both legs of the parity gate post the same file name
        SNAPSHOT=$PARITY_DIR/derby-snapshot.xml
    else
        SNAPSHOT=$(mktemp /tmp/derby-snapshot.xml.XXXXX)
    fi
    step snapshot_get

    # Resets database
    step --resets test-import-results.sh
    step test-each-role.sh

    step snapshot_put_as_nobody

    if [ "x$SKIP_PUT_SNAPSHOT" == "x" ]; then
        step snapshot_put
    else
        tput setaf 2  # green text
        echo Skipping snapshot put
        tput setaf 0  # black text
    fi

    rm -f $SNAPSHOT

    steps_done

    tput setaf 2  # green text
    echo "<<<<<<<<<<<<<<<<<<<<<<<< Test run complete >>>>>>>>>>>>>>>>>>>>>>>>"
    tput setaf 0  # black text
}

rm `dirname $0`/*.curl 2>&1 || true

if [ "$DBTYPE" == "none" ] ; then
    run_tests
elif [ "$DBTYPE" == "ez" ] ; then
    step ez_setup
    run_tests
elif [ "$DBTYPE" == "sqlite" ] ; then
    DBPATH=${1:-/Library/WebServer/Documents/xsite/local/trial.sqlite}
    prepare_for_setup
    curl_postj action.php \
        "action=setup.nodata&connection_string=sqlite:$DBPATH&dbuser=&dbpass=" \
        | check_jsuccess
    run_tests
elif [ "$DBTYPE" == "access" ] ; then
    prepare_for_setup
    curl_postj action.php \
              "action=setup.nodata&connection_string=odbc:DSN=gprm;Exclusive=NO&dbuser=&dbpass=" \
        | check_jsuccess

    # Access databases can't load a database snapshot, because it doesn't allow
    # primary key fields to be rewritten (I guess):
    #
    #  <exception>SQLSTATE[23000]: Integrity constraint violation: -1048
    #  [Microsoft][ODBC Microsoft Access Driver] Cannot update 'resultid'; field not
    #  updateable. (SQLExecute[-1048] at ext\pdo_odbc\odbc_stmt.c:254)</exception>

    export SKIP_PUT_SNAPSHOT=1
    run_tests
else
    tput setaf 1  # red text
    echo Unrecognized database type: $DBTYPE
    echo Known types are: sqlite access ez none
    tput setaf 0  # black text
    exit 1
fi
