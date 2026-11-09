#! /bin/bash

BASE_URL=$1
# The role logged in, as test-each-role.sh names it: RaceCoordinator, RaceCrew
# or anonymous.  Only the Django branch below reads it, and fails on any other.
ROLE=$2
set -e -E -o pipefail
source `dirname $0`/common.sh

# Every page answers 200, with no error text in it, whatever the role.  What a
# role is allowed to open is test-permissions.sh's business.
PAGES=(
    about.php
    awards-editor.php
    awards-presentation.php
    camera.php
    checkin.php
    coordinator.php
    # export-json.php
    export.php
    fake-timer.php
    fakeroster.php
    fullscreen.php
    history.php
    import-awards.php
    import-results.php
    import-roster.php
    import-snapshot.php
    index.php
    judging.php
    kiosk-dashboard.php
    login.php
    ondeck.php
    photo-thumbs.php
    playlist.php
    print.php
    racer-results.php
    racing-groups.php
    # replay.php
    scenes.php
    settings.php
    setup.php
    standings.php
    timer.php
    timer-test.php
    vote.php

    "kiosk.php?page=kiosks/award-presentations.kiosk"
    "kiosk.php?page=kiosks/identify.kiosk"
    "kiosk.php?page=kiosks/now-racing.kiosk"
    "kiosk.php?page=kiosks/ondeck.kiosk"
    "kiosk.php?page=kiosks/please-check-in.kiosk"
    "kiosk.php?page=kiosks/results-by-racer.kiosk"
    "kiosk.php?page=kiosks/welcome.kiosk"
)

for PAGE in "${PAGES[@]}" ; do
    # The Django server drops the printables (#55), so print.php answers 404.
    if [ "$DERBYNET_BACKEND" = django ] && [ "$PAGE" = print.php ] ; then
        curl_page_missing "$PAGE"
        continue
    fi
    curl_page "$PAGE" > /dev/null
done

# Should do a more complete test of the interaction between replay and the
# server.  This just ensures that the basic replay.message code hasn't broken
# completely.
if [ "$DERBYNET_BACKEND" = django ] ; then
    # D16: Django serves replay.message only to a role with CONTROL_RACE
    # (server/legacy_api/handlers/replay.py), which RaceCrew (8687) and the
    # anonymous role lack (server/accounts/defaults.py).
    # action.replay.message.inc has no permission check (line 4 is a "TODO:
    # Permissions?" comment); a run of test-each-role.sh against PHP with
    # testing/docker/php-scratch.sh passes as all three roles (#240).  The
    # request is the same; Django refuses it.
    case "$ROLE" in
        RaceCoordinator)
            curl_postj action.php "action=replay.message&status=-1&finished-replay=0" \
                | check_jsuccess
            ;;
        RaceCrew|anonymous)
            curl_postj action.php "action=replay.message&status=-1&finished-replay=0" \
                | jq -e '.outcome.summary == "failure" and .outcome.code == "notauthorized"' \
                     >/dev/null \
                || test_fails replay.message as $ROLE: expected notauthorized '(D16)'
            ;;
        *)
            test_fails "test-visit-each-page.sh: unknown role '$ROLE' as its" \
                       second argument, expected RaceCoordinator, RaceCrew or anonymous
            ;;
    esac
else
    curl_postj action.php "action=replay.message&status=-1&finished-replay=0" | check_jsuccess
fi
