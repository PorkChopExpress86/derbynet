#! /bin/bash

# Simulates the replay station, so we can watch the commands the server sends
# it.  Replay used to be a separate app the server pushed commands to over TCP,
# after a register-replay action; no server has that action now.  replay.php
# polls replay.message instead, about four times a second, and this does the
# same, printing each command (HELLO, START, RACE_STARTS, REPLAY, CANCEL, TEST)
# as it arrives.  Trigger one from another shell, e.g. with
# "action=replay.test", or race a heat.
#
# Usage: simulate-replay.sh <base url> [seconds]
#   It runs until stopped, or for the given number of seconds.
#
# It logs in as the coordinator, since the Django server serves replay only to
# a role that controls the race (D16).  It keeps its own cookie jar, so it
# can run beside another script.

BASE_URL=$1
set -e -E -o pipefail
NOANNOUNCE=1
source `dirname $0`/common.sh

# Its own files, not the shared cookies.curl and debug.curl
JAR=$(mktemp)
DEBUG_CURL=$(mktemp)
trap 'rm -f $JAR $DEBUG_CURL' EXIT

PASSWORD=$(sed -n -e "s/^RaceCoordinator://p" "${PASSWORDS_FILE:-`dirname $0`/default.passwords}")
curl -s -c $JAR -b $JAR -d "action=role.login&name=RaceCoordinator&password=$PASSWORD" \
     $BASE_URL/action.php | check_jsuccess

if [ -n "$2" ] ; then
    END=$(( $(date +%s) + $2 ))
fi

echo Polling replay.message as the replay station
while [ -z "$END" ] || [ "$(date +%s)" -lt "$END" ] ; do
    curl -s -c $JAR -b $JAR -d "action=replay.message&status=0&finished-replay=0" \
         $BASE_URL/action.php > $DEBUG_CURL
    jq -e '.outcome.summary == "success"' $DEBUG_CURL > /dev/null || test_fails replay.message failed
    jq -r --arg t "$(date +%T)" '.replay[] | "\($t) \(.)"' $DEBUG_CURL
    # replay.php polls every 250 ms
    timeout 0.25 tail -f /dev/null || true
done
