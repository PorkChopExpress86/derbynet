#! /bin/bash

BASE_URL=$1
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
    # The Django server drops the printables (#55), so it has no print.php.
    if [ "$DERBYNET_BACKEND" = django ] && [ "$PAGE" = print.php ] ; then
        continue
    fi
    curl_page "$PAGE" > /dev/null
done

# Should do a more complete test of the interaction between replay and the
# server.  This just ensures that the basic replay.message code hasn't broken
# completely.
curl_postj action.php "action=replay.message&status=-1&finished-replay=0" | check_jsuccess
