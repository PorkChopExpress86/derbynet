#! /bin/bash
BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# Which pages each role can open comes from fixtures/role-pages.json: an
# allowed page answers 200, a denied one redirects to the login page.  Whatever
# the server's navigation looks like, this is the contract.
FIXTURE="`dirname $0`/fixtures/role-pages.json"

# check_role_pages <role>, for whoever is logged in now.  Denied pages come
# first: PHP's kiosk.php adds the permission to present awards to the session, so
# once a kiosk page has been opened, awards-presentation.php opens too.  For the
# same reason, an anonymous visitor starts over afterward.
check_role_pages() {
    local PAGE RESULT
    for PAGE in $(jq -r --arg r "$1" '.[$r].denied[]' "$FIXTURE") ; do
        RESULT=$(curl_status $PAGE)
        [[ "$RESULT" =~ ^30[0-9]\ .*login ]] || \
            test_fails $1 is not redirected to login from $PAGE: $RESULT
    done
    for PAGE in $(jq -r --arg r "$1" '.[$r].allowed[]' "$FIXTURE") ; do
        RESULT=$(curl_status $PAGE)
        [[ "$RESULT" == "200 "* ]] || test_fails $1 cannot open $PAGE: $RESULT
    done
    if [ "$1" = anonymous ] ; then
        user_logout
    fi
}

## Every page, and every action

user_logout
check_role_pages anonymous

curl_postj action.php "action=kiosk.assign" | check_jfailure

curl_postj action.php "action=result.delete" | check_jfailure
curl_postj action.php "action=racer.edit" | check_jfailure
curl_postj action.php "action=result.write" | check_jfailure
curl_postj action.php "action=racer.import" | check_jfailure

# curl_postj action.php "action=role.login" | check_jfailure
curl_postj action.php "action=racer.add" | check_jfailure
curl_postj action.php "action=racer.pass" | check_jfailure
# TODO Replay application registers itself without credentials, a security weakness.
# curl_post action.php "action=register-replay" | check_failure
curl_postj action.php "action=replay.test" | check_jfailure
curl_postj action.php "action=schedule.reschedule" | check_jfailure
curl_postj action.php "action=schedule.generate" | check_jfailure
curl_postj action.php "action=heat.select" | check_jfailure
curl_post action.php "action=timer-message" | check_failure
curl_postj action.php "action=award.xbs" | check_jfailure

# Queries don't answer "<success/>" or "<failure/>", so there's really
# nothing to check other than that they parse as JSON.
curl_getj "action.php?query=class.list" > /dev/null
curl_getj "action.php?query=poll.coordinator" | jq . > /dev/null
curl_getj "action.php?query=poll.kiosk" > /dev/null
curl_getj "action.php?query=poll.kiosk.all" > /dev/null
curl_getj "action.php?query=poll&values=" > /dev/null

user_login_coordinator
check_role_pages RaceCoordinator
user_logout

# Logging out takes the coordinator's pages away again
check_role_pages anonymous

user_login_crew
check_role_pages RaceCrew
user_logout

check_role_pages anonymous
