# The master schedule: heats from several rounds interleaved by weight.  On a
# two-lane track a round has as many heats as it has racers (at least two), and
# the weight is round(heats / fewest heats).  With rounds of 2, 3, 5 and 9 racers
# the weights are 1, 1.5, 2.5 and 4.5, so every one lands exactly on a half,
# where PHP rounds away from zero and Python's round() goes to even.

use_session coord
reset_database
user_login_coordinator
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
post_ok "action=settings.write&use-master-sched-checkbox=1&use-master-sched=1"

# make_class <partition> <count> <first car number>
make_class() {
  local i
  for i in $(seq 1 "$2"); do
    post_ok "action=racer.import&firstname=$1$i&lastname=Racer&carnumber=$(($3 + i))&partition=$1"
  done
}
make_class Two 2 100
make_class Three 3 200
make_class Five 5 300
make_class Nine 9 400
post_ok "action=racer.bulk&who=all&what=checkin"

capture_scenario master-schedule/half-weights
capture racers "GET action.php?query=racer.list&order=car"
capture generate-round-1-two-racers "POST action.php action=schedule.generate&roundid=1"
capture generate-round-2-three-racers "POST action.php action=schedule.generate&roundid=2"
capture generate-round-3-five-racers "POST action.php action=schedule.generate&roundid=3"
capture generate-round-4-nine-racers "POST action.php action=schedule.generate&roundid=4"
capture rounds "GET action.php?query=poll&values=rounds"
capture ondeck-in-master-order "GET action.php?query=poll&values=ondeck,current-heat"

# Race the first few heats in master order, and the numbering of what is left
# must not move.
use_session timer
capture login-timer "POST action.php action=role.login&name=Timer&password="
use_session coord
post_ok "action=heat.select&heat=next-up&now_racing=1"
use_session timer
capture heartbeat-first-master-heat "POST action.php action=timer-message&message=HEARTBEAT"
capture finished-first-master-heat "POST action.php action=timer-message&message=FINISHED&lane1=3.100&lane2=3.200"
use_session coord
capture ondeck-after-one-heat "GET action.php?query=poll&values=ondeck,current-heat"
capture regenerate-round-4-after-a-heat-ran "POST action.php action=schedule.generate&roundid=4"
capture ondeck-after-regenerating "GET action.php?query=poll&values=ondeck,current-heat"

# A failed schedule.generate (#20 Q18, decided Fix in D30).  Race the heat with
# the highest master number out of order, so the numbers of the heats left are
# stale, then regenerate round 1, which has a result.  PHP refuses with
# results-exist but still renumbers the unraced heats of every round after the
# raced one, and moves the current heat to the first of them.
post_ok "action=heat.select&roundid=4&heat=9&now_racing=1"
use_session timer
curl_post action.php "action=timer-message&message=STARTED" | check_success
capture finished-last-master-heat-out-of-order "POST action.php action=timer-message&message=FINISHED&lane1=3.300&lane2=3.400"
use_session coord
capture coordinator-before-results-exist "GET action.php?query=poll.coordinator"
capture ondeck-before-results-exist "GET action.php?query=poll&values=ondeck,current-heat"
capture generate-round-1-results-exist "POST action.php action=schedule.generate&roundid=1"
capture coordinator-after-results-exist "GET action.php?query=poll.coordinator"
capture ondeck-after-results-exist "GET action.php?query=poll&values=ondeck,current-heat"
