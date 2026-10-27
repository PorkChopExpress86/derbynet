# schedule.generate for each round of a divided roster (testing/data/divided-roster.csv:
# Default 20 racers, Div 2 19, Div 3 10), then query=poll&values=schedule, under
# each scheduling setting.  A racer in a schedule is a racerid; each transcript
# starts with racer.list, which maps racerids to car numbers and names.

use_session coord
reset_database
user_login_coordinator
"$ROOT/testing/import-csv-roster.sh" "$BASE_URL" "$ROOT/testing/data/divided-roster.csv" >/dev/null 2>&1
post_ok "action=racer.bulk&who=all&what=checkin"

unschedule_all() {
  local r
  for r in 1 2 3; do post_ok "action=schedule.unschedule&roundid=$r"; done
}

# schedule_each_round <label> [extra generate parameters]
schedule_each_round() {
  local label="$1" extra="$2" r
  for r in 1 2 3; do
    capture "generate-round-$r" "POST action.php action=schedule.generate&roundid=$r$extra"
    curl_postj action.php "action=heat.select&roundid=$r&now_racing=0" >/dev/null
    capture "schedule-round-$r" "GET action.php?query=poll&values=schedule,rounds"
  done
}

start_variant() {   # <name>: a transcript that starts with the roster
  capture_scenario "schedule/$1"
  capture racers "GET action.php?query=racer.list&order=car"
}

start_variant normal-4-lanes
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
schedule_each_round normal
unschedule_all

start_variant two-runs-per-lane-4-lanes
schedule_each_round two-runs "&n_times_per_lane=2"
unschedule_all

start_variant cant-fulfill-4-lanes
# More runs per lane than the generator tables hold.  schedule.generate still
# reports success, because json_success() overwrites the failure.
capture generate-round-3-too-many-runs "POST action.php action=schedule.generate&roundid=3&n_times_per_lane=7"
capture select-round-3-with-no-heats "POST action.php action=heat.select&roundid=3&now_racing=0"
capture schedule-round-3-after-cant-fulfill "GET action.php?query=poll&values=schedule,rounds"
capture generate-round-3-at-the-limit "POST action.php action=schedule.generate&roundid=3&n_times_per_lane=6"
capture generate-round-99-no-such-round "POST action.php action=schedule.generate&roundid=99"
unschedule_all

# The same request over a chart (#20 Q1, #98): PHP deletes the chart, writes
# nothing, and replies success.
start_variant cant-fulfill-over-a-chart-4-lanes
capture generate-round-3-at-the-limit "POST action.php action=schedule.generate&roundid=3&n_times_per_lane=6"
capture select-round-3 "POST action.php action=heat.select&roundid=3&now_racing=0"
capture schedule-round-3-at-the-limit "GET action.php?query=poll&values=schedule,rounds"
capture generate-round-3-too-many-runs "POST action.php action=schedule.generate&roundid=3&n_times_per_lane=7"
capture schedule-round-3-after-too-many-runs "GET action.php?query=poll&values=schedule,rounds"
unschedule_all

# Zero runs per lane over a chart (#98): PHP deletes the chart and replies
# success with no heats.
start_variant zero-runs-over-a-chart-4-lanes
capture generate-round-3 "POST action.php action=schedule.generate&roundid=3"
capture select-round-3 "POST action.php action=heat.select&roundid=3&now_racing=0"
capture schedule-round-3 "GET action.php?query=poll&values=schedule,rounds"
capture generate-round-3-zero-runs "POST action.php action=schedule.generate&roundid=3&n_times_per_lane=0"
capture schedule-round-3-after-zero-runs "GET action.php?query=poll&values=schedule,rounds"
unschedule_all

start_variant rotation-schedule-4-lanes
post_ok "action=settings.write&rotation-schedule=1"
schedule_each_round rotation
unschedule_all
post_ok "action=settings.write&rotation-schedule=0"

start_variant max-runs-per-car-1-4-lanes
post_ok "action=settings.write&max-runs-per-car=1"
schedule_each_round max-runs
unschedule_all
post_ok "action=settings.write&max-runs-per-car=0"

start_variant unused-lane-mask-33-6-lanes
post_ok "action=settings.write&unused-lane-mask=33&n-lanes=6"
schedule_each_round mask33
unschedule_all

start_variant two-lanes
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
schedule_each_round two-lane
unschedule_all

start_variant seven-lanes
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=7"
schedule_each_round seven-lane
unschedule_all
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"

# More than 200 cars: testing/data/extended.csv has 200, and one more makes 201.
capture_scenario schedule/more-than-200-cars
reset_database
"$ROOT/testing/import-csv-roster.sh" "$BASE_URL" "$ROOT/testing/data/extended.csv" >/dev/null 2>&1
post_ok "action=racer.import&firstname=F-1201&lastname=L-1201&carnumber=1201&partition=TheTwoHundred"
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
capture generate-with-201-cars "POST action.php action=schedule.generate&roundid=1"
capture rounds-after-201-cars "GET action.php?query=poll&values=rounds"
post_ok "action=racer.delete&racer=201"
capture generate-with-200-cars "POST action.php action=schedule.generate&roundid=1"
capture rounds-after-200-cars "GET action.php?query=poll&values=rounds"
