# Standings and awards on a roster built to hit the edges issue #26 lists:
# an exact two-way tie, a three-way tie (so two empty rungs follow it), a racer
# with a single heat (the race is stopped partway), an excluded racer who is the
# fastest, and two racers whose last names differ only in case.  Each car runs at
# one fixed time in every heat, so ties are exact.

use_session coord
reset_database
user_login_coordinator
use_session timer
user_login_timer
use_session coord

post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
post_ok "action=racer.import&firstname=Ann&lastname=Lee&carnumber=101&partition=Edge"
post_ok "action=racer.import&firstname=Bob&lastname=lee&carnumber=102&partition=Edge"
post_ok "action=racer.import&firstname=Cy&lastname=Moss&carnumber=103&partition=Edge"
post_ok "action=racer.import&firstname=Di&lastname=Moss&carnumber=104&partition=Edge"
post_ok "action=racer.import&firstname=Ed&lastname=Moss&carnumber=105&partition=Edge"
post_ok "action=racer.import&firstname=Flo&lastname=Nye&carnumber=106&partition=Edge"
post_ok "action=racer.import&firstname=Gus&lastname=Oak&carnumber=107&partition=Edge"
post_ok "action=racer.import&firstname=Hal&lastname=Pine&carnumber=108&partition=Edge"
post_ok "action=racer.import&firstname=Ivy&lastname=Quill&carnumber=109&partition=Edge&exclude=yes"
post_ok "action=racer.bulk&who=all&what=checkin"
post_ok "action=schedule.generate&roundid=1"
post_ok "action=heat.select&roundid=1&now_racing=1"

export RACE_TIME_EXPR='({"101":3000,"102":3000,"103":3100,"104":3100,"105":3100,"106":3200,"107":3300,"108":3400,"109":2500}[(.carnumber|tostring)])'

capture_scenario standings/edge-cases-setup
capture racers "GET action.php?query=racer.list&order=car"
capture schedule "GET action.php?query=poll&values=schedule,rounds"

RACE_MAX_HEATS=6 race_round
capture_scenario standings/edge-cases-setup
capture rounds-after-six-heats "GET action.php?query=poll&values=rounds,current-heat"
standings_matrix standings/edge-cases-six-heats-of-nine

use_session coord
post_ok "action=heat.select&now_racing=1"
race_round
unset RACE_TIME_EXPR
standings_matrix standings/edge-cases-all-heats

# The lane bias paragraph of history.php, which lane_bias_analysis() feeds.
use_session coord
mkdir -p "$RAW/standings"
curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/history.php" \
  | sed -n '/<div id="lane-bias">/,/<h3>Events Timeline<\/h3>/p' \
  > "$RAW/standings/edge-cases-history-lane-bias.html"
