# The owner's race day of 06-race-day.sh on a 6-lane track (#20 "Byes fill a
# small final", #98).  Scouts round 1 races with times that tie cars 104 and 105
# at fourth place, so the top-4 final holds 5 racers and has a bye in every
# heat.  The schedule of each round is recorded once the playlist tees it up,
# with the standings that picked each final.  Roundids: Scouts round 1 is 1,
# Outlaws round 1 is 2, the Scouts final 3, the Outlaws final 4.

use_session coord
reset_database
user_login_coordinator
use_session timer
user_login_timer
use_session coord

post_ok "action=settings.write&unused-lane-mask=0&n-lanes=6"
for i in $(seq 1 10); do
  post_ok "action=racer.import&firstname=Scout$i&lastname=Racer&carnumber=$((100 + i))&partition=Scouts"
done
"$ROOT/testing/import-csv-roster.sh" "$BASE_URL" "$ROOT/testing/data/outlaw-class.csv" >/dev/null 2>&1
post_ok "action=racer.bulk&who=all&what=checkin"

capture_scenario race-day/six-lanes
capture racers "GET action.php?query=racer.list&order=car"
post_ok "action=playlist.add&classid=1&round=1&n_times_per_lane=1"
post_ok "action=playlist.add&classid=1&round=2&top=4&bucketed=0&n_times_per_lane=2"
post_ok "action=playlist.add&classid=2&round=1&n_times_per_lane=1"
post_ok "action=playlist.add&classid=2&round=2&top=8&bucketed=0&n_times_per_lane=2"
capture start-playlist "POST action.php action=playlist.start"
capture scouts-round-1 "GET action.php?query=poll&values=rounds,schedule,current-heat"

use_session timer
curl_post action.php "action=timer-message&message=HELLO" | check_success
curl_post action.php "action=timer-message&message=IDENTIFIED&lane_count=6&timer=TestTimer" | check_success
curl_post action.php "action=timer-message&message=HEARTBEAT" | check_success
use_session coord

# Each car's time depends on its car number only, and 105 runs as 104 does.
RACE_TIME_EXPR='2000 + ((if .carnumber == 105 then 104 else .carnumber end) - 100) * 10' race_round
capture standings-after-scouts-round-1 "GET action.php?query=standings"
capture scouts-final "GET action.php?query=poll&values=rounds,schedule,current-heat"

capture start-the-scouts-final "POST action.php action=heat.select&now_racing=1"
race_round 7
capture outlaws-round-1 "GET action.php?query=poll&values=rounds,schedule,current-heat"

capture start-outlaws-round-1 "POST action.php action=heat.select&now_racing=1"
race_round 3
capture standings-after-outlaws-round-1 "GET action.php?query=standings"
capture outlaws-final "GET action.php?query=poll&values=rounds,schedule,current-heat"

capture start-the-outlaws-final "POST action.php action=heat.select&now_racing=1"
race_round 5
capture standings-after-outlaws "GET action.php?query=standings"
