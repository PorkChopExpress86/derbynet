# Kiosk polling, for issue #34: poll.kiosk for twenty fixed addresses (the
# server derives each kiosk's two-word madlib name from its address), every
# built-in kiosk page assigned and polled, and poll.kiosk.all.

use_session coord
reset_database
user_login_coordinator
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=4"

capture_scenario kiosks/poll-kiosk
ADDRESSES="192.168.1.10 192.168.1.11 192.168.1.12 192.168.1.13 192.168.1.14 192.168.1.15
  192.168.1.16 192.168.1.17 192.168.1.18 192.168.1.19 10.0.0.1 10.0.0.2 10.0.0.254
  172.16.5.5 fe80::1 localhost 00:1A:2B:3C:4D:5E AA:BB:CC:DD:EE:FF kiosk-with-a-name 127.0.0.1"
for a in $ADDRESSES; do
  capture "first-poll-from-$a" "GET action.php?query=poll.kiosk&address=$a"
done
capture "poll-without-an-address" "GET action.php?query=poll.kiosk"
capture "poll-kiosk-all-after-twenty-addresses" "GET action.php?query=poll.kiosk.all"

capture_scenario kiosks/pages
for page in award-presentations derbynet flag identify now-racing-columnar-reversed now-racing-columnar \
            now-racing ondeck please-check-in qrcode results-by-racer scale-MPH slideshow standings welcome; do
  capture "assign-$page" "POST action.php action=kiosk.assign&address=192.168.1.10&page=kiosks/$page.kiosk"
  capture "poll-$page" "GET action.php?query=poll.kiosk&address=192.168.1.10"
done
capture "assign-with-parameters" "POST action.php action=kiosk.assign&address=192.168.1.10&page=kiosks/standings.kiosk&params=title=Final%20Standings"
capture "poll-with-parameters" "GET action.php?query=poll.kiosk&address=192.168.1.10"
capture "assign-name" "POST action.php action=kiosk.assign&address=192.168.1.10&name=Main"
capture "poll-named" "GET action.php?query=poll.kiosk&address=192.168.1.10"
capture "reload" "POST action.php action=kiosk.reload&address=192.168.1.10"
capture "poll-after-reload" "GET action.php?query=poll.kiosk&address=192.168.1.10"
capture "poll-kiosk-all" "GET action.php?query=poll.kiosk.all"
