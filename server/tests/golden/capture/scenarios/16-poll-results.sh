# query=poll.results, the results-by-racer kiosk's poll (#31): the schedule
# signature, the results of the current round (or of roundid) plus every
# result written in the last 300 s, the current heat and the next heat.  Every
# result here is written well inside the 300 s window, so a round raced before
# the current one still shows.  details=1 adds every column; with use-points
# the outcome is a place.

use_session coord
reset_database
user_login_coordinator
use_session timer
user_login_timer
use_session coord
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
for i in 1 2 3 4 5; do
  post_ok "action=racer.import&firstname=Lion$i&lastname=Racer&carnumber=$((100 + i))&partition=Lions"
  post_ok "action=racer.import&firstname=Tiger$i&lastname=Racer&carnumber=$((200 + i))&partition=Tigers"
done
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=schedule.generate&roundid=1"
post_ok "action=schedule.generate&roundid=2"

capture_scenario poll/poll-results
capture before-racing "GET action.php?query=poll.results"
post_ok "action=heat.select&roundid=1&now_racing=1"
capture current-round-not-raced "GET action.php?query=poll.results"
RACE_MAX_HEATS=2 race_round
capture after-two-heats "GET action.php?query=poll.results"
capture after-two-heats-details "GET action.php?query=poll.results&details=1"
race_round
post_ok "action=heat.select&roundid=2&now_racing=1"
capture next-round-keeps-recent-results "GET action.php?query=poll.results"
capture named-round "GET action.php?query=poll.results&roundid=1"
RACE_MAX_HEATS=1 race_round 7
set_checkbox use-points 1
capture under-points "GET action.php?query=poll.results"
set_checkbox use-points 0
