# Timer transcripts: what derby-timer.jar and the browser timer send to
# action=timer-message, and what the server answers, in order.  Every request
# carries a User-Agent.  The coordinator's own actions between messages are
# not part of a transcript; they only move the race along.

timer_msg() {   # timer_msg <name> <form fields after action=timer-message&message=>
  capture "$1" "POST action.php action=timer-message&message=$2"
}

coordinator() { use_session coord; CAPTURE_UA=; }
timer() { use_session timer; CAPTURE_UA="$TIMER_UA"; }

setup_four_lane_race() {   # <racers> <unused-lane-mask>
  reset_database
  use_session coord
  user_login_coordinator
  local i
  for i in $(seq 1 "$1"); do
    post_ok "action=racer.import&firstname=First10$i&lastname=Last10$i&carnumber=10$i&partition=Wolves"
  done
  post_ok "action=racer.bulk&who=all&what=checkin"
  post_ok "action=settings.write&unused-lane-mask=$2&n-lanes=4"
}

timer_login() {
  use_session timer
  capture login-timer "POST action.php action=role.login&name=Timer&password="
}

TIMER_UA="DerbyNet-Timer/golden-1.0 (jar)"
OTHER_UA="DerbyNet-Browser-Timer/golden-2.0"

setup_four_lane_race 6 0
CAPTURE_UA="$TIMER_UA"

capture_scenario timer/before-login
use_session anon
timer_msg hello-before-login "HELLO"
timer_msg heartbeat-before-login "HEARTBEAT"
timer_msg finished-before-login "FINISHED&lane1=3.000&lane2=3.100"

capture_scenario timer/handshake-and-idle-heartbeat
timer_login
timer_msg hello "HELLO&interface=golden-jar&build=1.2.3"
timer_msg identified-with-nlanes-as-the-suite-sends-it "IDENTIFIED&nlanes=4"
timer_msg identified "IDENTIFIED&lane_count=4&timer=TestTimer&human=Test%20Timer%20%26%20Co&ident=SN-0001&options=a:1,b:2&vid=0403&pid=6001"
timer_msg heartbeat-idle "HEARTBEAT"
timer_msg heartbeat-unhealthy "HEARTBEAT&unhealthy=1"
timer_msg heartbeat-healthy-again "HEARTBEAT"
timer_msg message-not-understood "NO-SUCH-MESSAGE"
timer_msg message-missing "&x=1"

capture_scenario timer/flags
timer_msg flags "FLAGS&flag-debug=bool:true&desc-debug=Debug%20output&flag-delay=int:5&desc-delay=Delay%20in%20ms&flag-name=string:lane%20one&ports=/dev/ttyUSB0,/dev/ttyUSB1&device-TestTimer=Test%20Timer&device-OtherTimer=Other%20Timer"
coordinator
capture timer-settings-as-coordinator "GET action.php?query=timer.settings"
timer
timer_msg heartbeat-after-flags "HEARTBEAT"

capture_scenario timer/race-staging-and-results
coordinator
post_ok "action=schedule.generate&roundid=1"
post_ok "action=heat.select&roundid=1&now_racing=1"
timer
timer_msg heartbeat-while-racing-is-on "HEARTBEAT"
timer_msg heartbeat-while-staging "HEARTBEAT"
timer_msg started "STARTED"
timer_msg finished-with-times "FINISHED&lane1=3.123&lane2=3.456&lane3=3.789&lane4=2.987"
timer_msg started-heat-2 "STARTED"
timer_msg finished-with-places-only "FINISHED&place1=2&place2=1&place3=4&place4=3"
timer_msg started-heat-3 "STARTED"
timer_msg finished-for-the-wrong-heat "FINISHED&roundid=1&heat=9&lane1=3.100&lane2=3.200&lane3=3.300&lane4=3.400"
timer_msg finished-naming-the-right-heat "FINISHED&roundid=1&heat=3&lane1=3.100&lane2=3.200&lane3=3.300&lane4=3.400"
timer_msg finished-with-times-and-places "FINISHED&lane1=3.500&lane2=3.600&lane3=3.700&lane4=3.800&place1=1&place2=2&place3=3&place4=4"
coordinator
capture poll-after-heats "GET action.php?query=poll.coordinator"

capture_scenario timer/finished-all-zeroes-and-not-racing
timer
timer_msg started-heat-5 "STARTED"
timer_msg finished-all-zeroes "FINISHED&lane1=0.000&lane2=0.000&lane3=0.000&lane4=0.000"
timer_msg finished-while-not-racing "FINISHED&lane1=3.100&lane2=3.200&lane3=3.300&lane4=3.400"
timer_msg heartbeat-after-racing-stopped "HEARTBEAT"
coordinator
capture poll-after-all-zeroes "GET action.php?query=poll.coordinator"

capture_scenario timer/unconfirmed-timer
coordinator
post_ok "action=heat.select&now_racing=1"
timer
timer_msg identified-unconfirmed "IDENTIFIED&lane_count=4&timer=TestTimer&confirmed=0"
timer_msg heartbeat-while-unconfirmed "HEARTBEAT&confirmed=0"
timer_msg heartbeat-confirmed "HEARTBEAT&confirmed=1"
timer_msg heartbeat-after-confirmed "HEARTBEAT"

capture_scenario timer/malfunction
timer_msg malfunction-detectable-by-default "MALFUNCTION&error=Lost%20the%20serial%20port"
timer_msg heartbeat-after-malfunction "HEARTBEAT"
timer_msg malfunction-detectable-1 "MALFUNCTION&detectable=1&error=Cable%20unplugged"
timer_msg malfunction-not-detectable "MALFUNCTION&detectable=0&error=Gate%20sensor%20silent"
timer_msg heartbeat-after-undetectable-malfunction "HEARTBEAT"
timer_msg malfunction-without-error-text "MALFUNCTION"
coordinator
capture poll-after-malfunction "GET action.php?query=poll.coordinator"
timer
timer_msg hello-after-malfunction "HELLO"
timer_msg identified-after-malfunction "IDENTIFIED&lane_count=4&timer=TestTimer"

capture_scenario timer/remote-start
coordinator
capture remote-start-before-the-timer-offers-it "POST action.php action=timer.remote-start"
timer
timer_msg hello-offering-remote-start "HELLO&remote-start=YES"
timer_msg heartbeat-offering-remote-start "HEARTBEAT&remote-start=YES"
coordinator
capture remote-start-requested "POST action.php action=timer.remote-start"
timer
timer_msg heartbeat-after-remote-start-request "HEARTBEAT&remote-start=YES"
timer_msg heartbeat-after-remote-start-delivered "HEARTBEAT&remote-start=YES"
timer_msg heartbeat-withdrawing-remote-start "HEARTBEAT"

capture_scenario timer/competing-timers
CAPTURE_UA="$TIMER_UA"
timer_msg first-timer-hello "HELLO"
CAPTURE_UA="$OTHER_UA"
timer_msg second-timer-hello-within-a-second "HELLO"
timer_msg second-timer-heartbeat "HEARTBEAT"
CAPTURE_UA="$TIMER_UA"
timer_msg first-timer-heartbeat-is-told-it-competes "HEARTBEAT"
CAPTURE_UA="$OTHER_UA"
timer_msg second-timer-heartbeat-again "HEARTBEAT"
CAPTURE_UA="$TIMER_UA"

capture_scenario timer/reverse-lanes
setup_four_lane_race 3 1
post_ok "action=schedule.generate&roundid=1"
post_ok "action=heat.select&roundid=1&now_racing=1"
timer_login
timer_msg hello "HELLO"
timer_msg identified "IDENTIFIED&lane_count=4&timer=TestTimer"
timer_msg heartbeat-lane-mask-as-scheduled "HEARTBEAT"
coordinator
post_ok "action=heat.select&roundid=1&now_racing=0"
post_ok "action=settings.write&reverse-lanes=1&reverse-lanes-checkbox=1"
post_ok "action=heat.select&roundid=1&now_racing=1"
timer
timer_msg heartbeat-lane-mask-reversed "HEARTBEAT"
timer_msg started "STARTED"
timer_msg finished-lanes-reversed "FINISHED&lane2=3.200&lane3=3.300&lane4=3.400"
coordinator
capture poll-after-reversed-finish "GET action.php?query=poll.coordinator"

CAPTURE_UA=
