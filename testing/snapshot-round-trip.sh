#! /bin/bash

# Takes a snapshot, changes the database, then puts the snapshot back: refused
# as nobody, accepted as the coordinator.  The end of exercise-one-database.sh,
# on its own.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

`dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/test-import-results.sh "$BASE_URL"
user_login_coordinator

SNAPSHOT=$(mktemp /tmp/derby-snapshot.xml.XXXXX)
curl_get_xml "action.php?query=snapshot.get" > $SNAPSHOT

`dirname $0`/test-import-results.sh "$BASE_URL"
user_logout
curl_put_snapshot $SNAPSHOT | check_failure
user_login_coordinator
curl_put_snapshot $SNAPSHOT | check_success
