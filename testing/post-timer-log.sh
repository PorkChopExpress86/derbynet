#! /bin/bash
# Reads data from stdin and appends it to the server's timer log, as
# derby-timer.jar does.  It logs in as the timer first (S22: the server refuses
# an anonymous upload).  Prints the server's reply.
#
# Usage: post-timer-log.sh <base url> < log-text

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_timer

curl -s -b $COOKIES_CURL -c $COOKIES_CURL -X POST -H 'Content-Type: text/plain' --data-binary @- \
     $BASE_URL/post-timer-log.php
