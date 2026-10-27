# Heat-state quirks that the other scenarios do not reach (#27).  Each case
# starts from an empty database: six Wolves on 4 lanes, round 1 scheduled and
# racing, with the timer posting each heat's results.
#
#   Q11  a FINISHED that leaves a scheduled lane unposted, then the poll
#   Q16  a FINISHED whose lane 1 time is "---", then that heat's results
#   Q4   a rerun and a reinstate under points racing, with times and places

heat_state_case() {   # heat_state_case: a fresh event racing heat 1
  use_session coord
  reset_database
  user_login_coordinator
  use_session timer
  user_login_timer
  use_session coord
  post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"
  local i
  for i in 1 2 3 4 5 6; do
    post_ok "action=racer.import&firstname=First10$i&lastname=Last10$i&carnumber=10$i&partition=Wolves"
  done
  post_ok "action=racer.bulk&who=all&what=checkin"
  post_ok "action=schedule.generate&roundid=1"
}

heat_state_timer() {   # heat_state_timer <name> <fields after message=>
  use_session timer
  capture "$1" "POST action.php action=timer-message&message=$2"
  use_session coord
}

heat_state_case
post_ok "action=heat.select&roundid=1&now_racing=1"
capture_scenario timer/partial-and-non-numeric-results
heat_state_timer finished-without-lane-4 "FINISHED&lane1=3.100&lane2=3.200&lane3=3.300"
capture poll-after-a-lane-without-a-result "GET action.php?query=poll.coordinator&roundid=1&heat=1"
heat_state_timer finished-with-a-dashed-time "FINISHED&lane1=---&lane2=3.200&lane3=3.300&lane4=3.400"
capture heat-results-with-a-dashed-time "GET action.php?query=poll.coordinator&roundid=1&heat=2"
capture standings-with-a-dashed-time "GET action.php?query=standings"

heat_state_case
post_ok "action=settings.write&use-points=1&use-points-checkbox"
post_ok "action=heat.select&roundid=1&now_racing=1"
capture_scenario suite/points-rerun-reinstate
heat_state_timer finished-with-times-and-places "FINISHED&lane1=3.400&lane2=3.300&lane3=3.200&lane4=3.100&place1=4&place2=3&place3=2&place4=1"
capture rerun-last-heat "POST action.php action=heat.rerun&heat=last"
capture reinstate-under-points "POST action.php action=heat.reinstate"
