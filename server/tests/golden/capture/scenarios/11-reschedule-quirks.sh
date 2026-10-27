# Reschedule cases that test-reschedule.sh does not reach (#24): what PHP does
# with racing state and with a search that runs out of added heats.  Each case
# starts from an empty database on 4 lanes, with racer ids 1, 2, ... in import
# order, and races its first heats with the timer.  A reply that fits no racer
# in, or answers nochart, shows no runs in its timing, so those requests send
# trace=1, and the reschedule-timing rule of normalize.mjs reads the runs per
# lane from the adjuster trace.

reschedule_case() {   # reschedule_case <racers> <partition>: a fresh event
  use_session coord
  reset_database
  user_login_coordinator
  use_session timer
  user_login_timer
  use_session coord
  post_ok "action=settings.write&unused-lane-mask=${MASK:-0}&n-lanes=4"
  local i
  for i in $(seq 1 "$1"); do
    post_ok "action=racer.import&firstname=$2$i&lastname=Racer&carnumber=$((100 + i))&partition=$2"
  done
}

# Q5: a dry run that squeezes out a heat of the round being raced.  Racer 10
# leaves after heat 1, so its four lanes become byes, and PHP squeezes out
# heat 9, whose racers fill them; heats 2 and 3 are unchanged.  PHP turns
# racing off although the dry run changes nothing.
reschedule_case 11 Racers
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=schedule.generate&roundid=1"
post_ok "action=heat.select&roundid=1&now_racing=1"
RACE_MAX_HEATS=1 race_round
post_ok "action=racer.pass&racer=10&value=0"
capture_scenario reschedule/dry-run-while-racing
capture racing-before "GET action.php?query=poll.coordinator"
capture dry-run-squeezes-a-heat "POST action.php action=schedule.reschedule&roundid=1&dry-run=1&trace=1"
capture racing-after "GET action.php?query=poll.coordinator"

# Q6: under the master schedule, a late racer is fitted into the round being
# raced.  PHP's branch that would stop racing reads a variable it never sets,
# so racing and the current heat stay.
reschedule_case 6 Racers
post_ok "action=settings.write&use-master-sched-checkbox=1&use-master-sched=1"
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=racer.pass&racer=6&value=0"
post_ok "action=schedule.generate&roundid=1"
post_ok "action=heat.select&heat=next-up&now_racing=1"
RACE_MAX_HEATS=2 race_round
post_ok "action=racer.pass&racer=6&value=1"
capture_scenario reschedule/master-schedule
capture racing-before "GET action.php?query=poll.coordinator"
capture late-racer-in-the-running-round "POST action.php action=schedule.reschedule&roundid=1"
capture racing-after "GET action.php?query=poll.coordinator"

# Q21: two racers on three lanes (lane 2 unused), two runs per lane, three
# heats raced.  Racer 5 needs a new heat in every lane for its second run, and
# PHP never searches with the last heat it adds, so it answers nochart.  The
# race then moves to round 2, so no heat of round 1 is running or staged.
MASK=2 reschedule_case 2 Lions
post_ok "action=racer.import&firstname=Tiger1&lastname=Racer&carnumber=201&partition=Tigers"
post_ok "action=racer.import&firstname=Tiger2&lastname=Racer&carnumber=202&partition=Tigers"
post_ok "action=racer.import&firstname=Lions3&lastname=Racer&carnumber=103&partition=Lions"
post_ok "action=racer.pass&racer=1&value=1"
post_ok "action=racer.pass&racer=2&value=1"
post_ok "action=racer.pass&racer=3&value=1"
post_ok "action=racer.pass&racer=4&value=1"
post_ok "action=schedule.generate&roundid=1&n_times_per_lane=2"
post_ok "action=schedule.generate&roundid=2"
post_ok "action=heat.select&roundid=1&now_racing=1"
RACE_MAX_HEATS=3 race_round
post_ok "action=heat.select&roundid=2&heat=1"
capture_scenario reschedule/last-added-heat
capture rounds-before "GET action.php?query=poll.coordinator"
capture dry-run-runs-out-of-added-heats "POST action.php action=schedule.reschedule&roundid=1&racerid=5&dry-run=1&trace=1"
