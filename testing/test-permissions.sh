#! /bin/bash
BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# Which pages each role can open comes from fixtures/role-pages.json: an
# allowed page answers 200, a denied one redirects to the login page.  Whatever
# the server's navigation looks like, this is the contract.
FIXTURE="`dirname $0`/fixtures/role-pages.json"

# With DERBYNET_BACKEND=django, fixtures/role-pages-django.json moves the pages
# whose access Django decided differently into the other list, each with the
# quirk id or owner decision that says why (testing/README.md).  Every old page
# URL of Django answers a 301 to its rebuilt page (D20), so the Django leg
# follows that one redirect and judges the page it lands on: an allowed page
# answers 200, a denied one a 302 to the login page.  A second redirect, or one
# to another server, fails.
DJANGO_FIXTURE="`dirname $0`/fixtures/role-pages-django.json"

# role_pages <role> <allowed|denied>: the pages of that list, one per line.
role_pages() {
    if [ "$DERBYNET_BACKEND" = django ] ; then
        jq -r -n --arg r "$1" --arg k "$2" \
           --slurpfile php "$FIXTURE" --slurpfile dj "$DJANGO_FIXTURE" '
            (if $k == "allowed" then "denied" else "allowed" end) as $other
            | ($dj[0][$r] // {}) as $moved
            | ($php[0][$r][$k] - (($moved[$other] // {}) | keys))
              + (($moved[$k] // {}) | keys)
            | .[]'
    else
        jq -r --arg r "$1" ".[\$r].$2[]" "$FIXTURE"
    fi
}

# Every page moved in the Django fixture must be in the other list of the PHP
# fixture, and no role may list a page twice, so each page is checked once.
if [ "$DERBYNET_BACKEND" = django ] ; then
    jq -e -n --slurpfile php "$FIXTURE" --slurpfile dj "$DJANGO_FIXTURE" '
        ($php[0] | to_entries | all(.value.allowed + .value.denied
                                    | length == (unique | length)))
        and ($dj[0] | to_entries | all(.key as $r | .value | to_entries
              | all(.key as $k | .value | keys
                    | all(. as $page
                          | $php[0][$r][if $k == "allowed" then "denied"
                                        else "allowed" end]
                          | index([$page]) != null))))' > /dev/null || \
        test_fails "$DJANGO_FIXTURE" moves a page the PHP fixture does not \
                   have in the other list, or a role lists a page twice
    [[ "$BASE_URL" =~ ^[a-z]+://[^/]+ ]] && SERVER_ORIGIN="${BASH_REMATCH[0]}"
fi

# page_status <page>: what curl_status prints for the page.  On Django, a 301
# is followed once: "301 <target> then <status of the target>".
page_status() {
    local RESULT
    RESULT=$(curl_status $1)
    if [ "$DERBYNET_BACKEND" = django ] && [[ "$RESULT" == "301 "* ]] ; then
        local TARGET="${RESULT#301 }"
        if [[ "$TARGET" != "$SERVER_ORIGIN/"* ]] ; then
            echo "$RESULT, off this server"
            return
        fi
        RESULT="$RESULT then $(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' \
                                    -b $COOKIES_CURL -c $COOKIES_CURL "$TARGET")"
    fi
    echo "$RESULT"
}

# check_role_pages <role>, for whoever is logged in now.  Denied pages come
# first: PHP's kiosk.php adds the permission to present awards to the session, so
# once a kiosk page has been opened, awards-presentation.php opens too.  For the
# same reason, an anonymous visitor starts over afterward.  Django's kiosk.php
# grants nothing (S28); the order stays, for one script.
check_role_pages() {
    local PAGE RESULT FINAL DENIED=0 ALLOWED=0
    # PHP's denied page answers any redirect to login.  Django's ends in a 302.
    local DENIED_RULE='^30[0-9] .*login'
    [ "$DERBYNET_BACKEND" = django ] && DENIED_RULE='^302 .*login'
    for PAGE in $(role_pages "$1" denied) ; do
        RESULT=$(page_status $PAGE)
        FINAL="${RESULT##* then }"
        [[ "$FINAL" =~ $DENIED_RULE ]] || \
            test_fails $1 is not redirected to login from $PAGE: $RESULT
        DENIED=$((DENIED + 1))
    done
    for PAGE in $(role_pages "$1" allowed) ; do
        RESULT=$(page_status $PAGE)
        FINAL="${RESULT##* then }"
        [[ "$FINAL" == "200 "* ]] || test_fails $1 cannot open $PAGE: $RESULT
        ALLOWED=$((ALLOWED + 1))
    done
    if [ "$DERBYNET_BACKEND" = django ] ; then
        local EXPECTED=$(jq --arg r "$1" '.[$r].allowed + .[$r].denied | length' "$FIXTURE")
        [ $((DENIED + ALLOWED)) -eq "$EXPECTED" ] || \
            test_fails $1: checked $((DENIED + ALLOWED)) pages, the fixture has $EXPECTED
        echo "$1: checked $((DENIED + ALLOWED)) of $EXPECTED pages," \
             "$DENIED denied and $ALLOWED allowed"
    fi
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
