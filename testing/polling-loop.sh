#! /bin/bash

# For load-testing the server, try nonstop poll.coordinator queries.  Run
# several at once for a better load.  poll.coordinator needs no login.  It
# prints a line for every 100 polls, and one for each poll that doesn't answer
# with HTTP status 200.
#
# Usage: polling-loop.sh <base url>

BASE_URL=$1
if [ -z "$BASE_URL" ] ; then
    echo Base URL required!
    exit 1
fi

N=0
while true ; do
    STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/action.php?query=poll.coordinator" || true)
    if [ "$STATUS" != 200 ] ; then
        echo "poll.coordinator answered with HTTP status $STATUS" >&2
    fi
    N=$((N + 1))
    if [ $((N % 100)) -eq 0 ] ; then
        echo "$N polls"
    fi
done
