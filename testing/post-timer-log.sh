#! /bin/sh
# Reads data from stdin.  Posts it as the session in the cookie jar, which
# must hold the timer's login (S22: the server refuses an anonymous upload).

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

curl -s -b $COOKIES_CURL -c $COOKIES_CURL -X POST -H 'Content-Type: text/plain' --data-binary @- \
     $BASE_URL/post-timer-log.php
