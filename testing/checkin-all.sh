#! /bin/bash

# Checks in every racer, one racer.pass each, in racerid order, as the session
# in the cookie jar (demo.sh and demo-multi.sh log in as the coordinator first).
# It asks for the racerids, because the Django server never reuses one after a
# reset.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

for RACERID in $(lookup_get "action.php?query=racer.list" | jq '.racers[].racerid' | sort -n) ; do
    curl_postj action.php "action=racer.pass&racer=$RACERID&value=1" | check_jsuccess
done
