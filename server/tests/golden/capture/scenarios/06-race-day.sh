# The owner's race day: one class at a time, a first round, then a final of the
# top 4 or top 8, queued in a playlist.  Scouts race round 1 and a top-4 final,
# then Outlaws (testing/data/outlaw-class.csv, 14 racers) race round 1 and a
# top-8 final.  The PHP playlist tees the next round up and waits for the
# operator, which is the intermission the Django port will make explicit.
#
# Poll payloads are recorded at fixed points along the way: an empty database,
# after check-in, mid-heat, after a result, between rounds, and after awards
# are presented.

KIOSK=GOLDEN-KIOSK-1
ALL_VALUES="current-heat,racers,rounds,classes,partitions,ondeck,heat-results,timer-state,replay-state,last-heat,schedule,race-structure,precision,best-times,car-numbers,current-reschedule,timer-trouble"

capture_polls() {   # capture_polls <label>: the three poll flavours at this moment
  use_session coord
  capture "$1 poll.coordinator" "GET action.php?query=poll.coordinator"
  capture "$1 poll.kiosk" "GET action.php?query=poll.kiosk&address=$KIOSK"
  capture "$1 poll" "GET action.php?query=poll&values=$ALL_VALUES"
}

use_session coord
reset_database
user_login_coordinator
use_session timer
user_login_timer
use_session coord

capture_scenario poll/race-day-polls
capture_polls "01-empty-database"
capture "01-empty-database poll.kiosk.all" "GET action.php?query=poll.kiosk.all"

post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
for i in $(seq 1 10); do
  post_ok "action=racer.import&firstname=Scout$i&lastname=Racer&carnumber=$((100 + i))&partition=Scouts"
done
"$ROOT/testing/import-csv-roster.sh" "$BASE_URL" "$ROOT/testing/data/outlaw-class.csv" >/dev/null 2>&1
capture_polls "02-roster-imported-nobody-checked-in"

post_ok "action=racer.bulk&who=all&what=checkin"
capture "03-checked-in racers" "GET action.php?query=racer.list&order=car"
capture_polls "03-checked-in"

# The playlist: Scouts round 1, Scouts final (top 4), Outlaws round 1, Outlaws final (top 8).
capture_scenario race-day/playlist-queue
capture add-scouts-round-1 "POST action.php action=playlist.add&classid=1&round=1&n_times_per_lane=1"
capture add-scouts-final-top-4 "POST action.php action=playlist.add&classid=1&round=2&top=4&bucketed=0&n_times_per_lane=2"
capture add-outlaws-round-1 "POST action.php action=playlist.add&classid=2&round=1&n_times_per_lane=1"
capture add-outlaws-final-top-8 "POST action.php action=playlist.add&classid=2&round=2&top=8&bucketed=0&n_times_per_lane=2"
capture start-playlist "POST action.php action=playlist.start"
capture rounds-after-start "GET action.php?query=poll&values=rounds,schedule,current-heat"

capture_scenario poll/race-day-polls
capture_polls "04-scheduled-and-racing-heat-1"
use_session timer
capture timer-hello "POST action.php action=timer-message&message=HELLO"
capture timer-identified "POST action.php action=timer-message&message=IDENTIFIED&lane_count=4&timer=TestTimer"
capture timer-heartbeat-staging "POST action.php action=timer-message&message=HEARTBEAT"
capture timer-started "POST action.php action=timer-message&message=STARTED"
capture_polls "05-mid-heat"
use_session timer
capture timer-finished "POST action.php action=timer-message&message=FINISHED&lane1=2.411&lane2=2.522&lane3=2.633&lane4=2.744"
use_session coord
capture_polls "06-after-a-result"
capture "06-after-a-result poll heat-results" "GET action.php?query=poll&values=heat-results,last-heat&roundid=1&heat=1"

# Race the rest of Scouts round 1.  The playlist then tees up the top-4 final.
race_round
capture_scenario race-day/scouts
capture standings-after-scouts-round-1 "GET action.php?query=standings"
capture rounds-between-scouts-rounds "GET action.php?query=poll&values=rounds,schedule,current-heat,classes"
capture_scenario poll/race-day-polls
capture_polls "07-between-rounds-final-teed-up"

capture_scenario race-day/scouts
capture start-the-scouts-final "POST action.php action=heat.select&now_racing=1"
race_round 7
capture rounds-after-scouts-final "GET action.php?query=poll&values=rounds,schedule,current-heat"
capture standings-after-scouts-final "GET action.php?query=standings"
capture racers-after-scouts "GET action.php?query=racer.list&order=car"

# Outlaws
capture_scenario race-day/outlaws
capture start-outlaws-round-1 "POST action.php action=heat.select&now_racing=1"
race_round 3
capture rounds-after-outlaws-round-1 "GET action.php?query=poll&values=rounds,schedule,current-heat"
capture start-the-outlaws-final "POST action.php action=heat.select&now_racing=1"
race_round 5
capture rounds-after-outlaws-final "GET action.php?query=poll&values=rounds,schedule,current-heat"
capture standings-after-outlaws "GET action.php?query=standings"
capture racers-after-outlaws "GET action.php?query=racer.list&order=car"
capture award-list "GET action.php?query=award.list"

capture_scenario poll/race-day-polls
capture "08-before-awards award.current" "GET action.php?query=award.current"
capture present-speed-1 "POST action.php action=award.present&key=speed-1"
capture_polls "08-award-presented"
capture "08-award-presented award.current" "GET action.php?query=award.current"
capture reveal-award "POST action.php action=award.present&reveal=1"
capture "08-award-revealed award.current" "GET action.php?query=award.current"
capture "08-award-revealed award.list" "GET action.php?query=award.list"
