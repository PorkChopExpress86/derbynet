# The replay command sequence: a replay client polls action=replay.message and
# is handed, in order, HELLO, CANCEL, START, RACE_STARTS, REPLAY and the rest as
# the race moves along.  The queue only fills while a client has polled within
# the last ten seconds.

# The replay queue can live in a file that a database reset does not empty, so
# earlier scenarios may have left commands in it.  Drain it, then reset again so
# the first poll below is the first replay contact.
reset_database
curl_postj action.php "action=replay.message&status=0&finished-replay=0" >/dev/null
reset_database
use_session coord
user_login_coordinator
use_session timer
user_login_timer
use_session coord
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
for i in 1 2 3 4 5; do
  post_ok "action=racer.import&firstname=Replay$i&lastname=Racer&carnumber=20$i&partition=Wolves"
done
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=schedule.generate&roundid=1"

replay_poll() {   # replay_poll <name> [status]
  use_session replay
  capture "$1" "POST action.php action=replay.message&status=${2:-0}&finished-replay=0"
  use_session coord
}

capture_scenario replay/command-sequence
use_session replay
capture replay-first-poll-is-greeted "POST action.php action=replay.message&status=-2&finished-replay=0"
use_session coord
post_ok "action=heat.select&roundid=1&now_racing=1"
replay_poll replay-after-heat-select-sees-cancel-and-start 0
post_ok "action=heat.select&roundid=1&now_racing=0"
post_ok "action=heat.select&roundid=1&now_racing=1"
replay_poll replay-after-racing-off-and-on 1
use_session timer
capture timer-started "POST action.php action=timer-message&message=STARTED"
replay_poll replay-after-gate-opens 1
use_session timer
capture timer-finished "POST action.php action=timer-message&message=FINISHED&lane1=3.111&lane2=3.222&lane3=3.333&lane4=3.444"
replay_poll replay-after-finish-playing 2
replay_poll replay-after-playback-finished 0
use_session coord
capture rerun-last-heat "POST action.php action=heat.rerun&heat=last"
replay_poll replay-after-rerun 0
capture replay-trigger "POST action.php action=replay.trigger"
replay_poll replay-after-trigger 0
capture replay-test "POST action.php action=replay.test"
replay_poll replay-after-test 0
capture coordinator-poll-with-replay-connected "GET action.php?query=poll.coordinator"

post_ok "action=settings.write&replay-skipback=2500&replay-num-showings=3&replay-rate=25"
capture replay-trigger-with-other-settings "POST action.php action=replay.trigger"
replay_poll replay-after-trigger-with-other-settings 0
use_session replay
capture replay-poll-with-no-status "POST action.php action=replay.message"

# A client that stops polling for more than ten seconds is forgotten, and is
# greeted again when it comes back.
sleep 11
replay_poll replay-greeted-again-after-silence 0
