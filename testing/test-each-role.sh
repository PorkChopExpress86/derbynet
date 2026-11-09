#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# The second argument names the role, for the Django branch of
# test-visit-each-page.sh (D16).
user_login_coordinator
`dirname $0`/test-visit-each-page.sh $BASE_URL RaceCoordinator

user_login_crew
`dirname $0`/test-visit-each-page.sh $BASE_URL RaceCrew

user_logout
`dirname $0`/test-visit-each-page.sh $BASE_URL anonymous
