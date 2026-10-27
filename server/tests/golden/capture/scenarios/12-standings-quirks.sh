# Standings and award quirks that #20 assigns to #26, each on a roster of its own:
#
#   Q7   add_speed_group() indexes $recipients[$i] after array_diff() kept the
#        original keys, so a rung [A, B] whose A already holds a trophy under
#        one-trophy-per awards the trophy to a null racer and skips B.
#   Q13  standings.inc writes scoring=1 when the legacy drop-slowest key is set
#        and scoring is not.
#
# and the award settings the other standings fixtures leave out: full-field-calc
# at 0, -1 and an aggregate's id, group and subgroup ntrophies, one-trophy-per.

use_session timer
user_login_timer

# ----- Q7: one group with subgroups Lions and Tigers; Abbot (Lions) and Baker
# (Tigers) tie in every heat.  The nonracing aggregate "Lions Only" holds Lions
# alone, and add_speed_awards() awards aggregates before groups, so Abbot wins it
# and is on the no-trophies list when the group's first rung is [Abbot, Baker].
# #20's layout, an aggregate over both groups, cannot show Q7: the tied racers
# tie in the aggregate too, a tied place awards every racer of its rung, so both
# hold a trophy and the group's rung has nobody left.  The aggregate has to hold
# the tie's first racer without the second, so it draws from one subgroup.
use_session coord
reset_database
user_login_coordinator
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
post_ok "action=racer.import&firstname=Ann&lastname=Abbot&carnumber=301&partition=Lions"
post_ok "action=racer.import&firstname=Ben&lastname=Baker&carnumber=302&partition=Tigers"
post_ok "action=partition.apply-rule&rule=one-group"
post_ok "action=class.add&name=Lions Only&rankid_$(rankid_of Lions)=1"
set_checkbox one-trophy-per 1
post_ok "action=settings.write&n-pack-trophies=0&n-den-trophies=1"
post_ok "action=racer.bulk&who=all&what=checkin"
round="$(roundid_of 'All Racers' 1)"
post_ok "action=schedule.generate&roundid=$round"
post_ok "action=heat.select&roundid=$round&now_racing=1"
RACE_TIME_EXPR='3000' race_round

capture_scenario standings/q7-tie-after-earlier-trophy
capture racers "GET action.php?query=racer.list&order=car"
capture classes "GET action.php?query=class.list"
capture export-json "GET export-json.php"
capture standings "GET action.php?query=standings"
capture award.list "GET action.php?query=award.list"

# ----- Award settings: groups Red and Blue, and the aggregate Both over them.
# Each setting stays in place for the exchanges after it.
use_session coord
reset_database
user_login_coordinator
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
post_ok "action=racer.import&firstname=Cal&lastname=Ames&carnumber=201&partition=Red"
post_ok "action=racer.import&firstname=Dot&lastname=Bell&carnumber=202&partition=Red"
post_ok "action=racer.import&firstname=Eve&lastname=Cobb&carnumber=203&partition=Red"
post_ok "action=racer.import&firstname=Fay&lastname=Dunn&carnumber=204&partition=Blue"
post_ok "action=racer.import&firstname=Gil&lastname=Eads&carnumber=205&partition=Blue"
post_ok "action=racer.import&firstname=Hap&lastname=Fox&carnumber=206&partition=Blue"
post_ok "action=class.add&name=Both&constituent_$(classid_of Red)=1&constituent_$(classid_of Blue)=1"
set_checkbox do-use-subgroups 1
post_ok "action=racer.bulk&who=all&what=checkin"
for group in Red Blue; do
  round="$(roundid_of $group 1)"
  post_ok "action=schedule.generate&roundid=$round"
  post_ok "action=heat.select&roundid=$round&now_racing=1"
  RACE_TIME_EXPR='({"201":2900,"202":3000,"203":3300,"204":2950,"205":3100,"206":3200}[(.carnumber|tostring)])' race_round
done

capture_scenario standings/award-settings
capture racers "GET action.php?query=racer.list&order=car"
capture classes "GET action.php?query=class.list"
for calc in 0 -1 Both; do
  value="$calc"
  [ "$calc" != Both ] || value="$(classid_of Both)"
  capture_ok "full-field-calc=$calc award.calc-rule" "POST action.php action=award.calc-rule&full-field-calc=$value"
  capture "full-field-calc=$calc award.list" "GET action.php?query=award.list"
  capture "full-field-calc=$calc export-json" "GET export-json.php"
done
capture_ok "full-field-calc=0 again award.calc-rule" "POST action.php action=award.calc-rule&full-field-calc=0"
capture_ok "group-ntrophies=1 class.edit" "POST action.php action=class.edit&classid=$(classid_of Red)&name=Red&ntrophies=1"
capture "group-ntrophies=1 award.list" "GET action.php?query=award.list"
capture "group-ntrophies=1 export-json" "GET export-json.php"
capture_ok "subgroup-ntrophies=2 rank.edit" "POST action.php action=rank.edit&rankid=$(rankid_of Blue)&name=Blue&ntrophies=2"
capture "subgroup-ntrophies=2 award.list" "GET action.php?query=award.list"
capture "subgroup-ntrophies=2 export-json" "GET export-json.php"
for one in 1 0; do
  set_checkbox one-trophy-per $one
  capture "one-trophy-per=$one award.list" "GET action.php?query=award.list"
  capture "one-trophy-per=$one export-json" "GET export-json.php"
done

# ----- Q13: two racers whose order depends on dropping the slowest heat.  Their
# snapshot, with drop-slowest=1 and no scoring in RaceInfo, is loaded back.
use_session coord
reset_database
user_login_coordinator
post_ok "action=settings.write&unused-lane-mask=0&n-lanes=2"
post_ok "action=racer.import&firstname=Ida&lastname=Gray&carnumber=401&partition=Snapshot"
post_ok "action=racer.import&firstname=Jo&lastname=Hart&carnumber=402&partition=Snapshot"
post_ok "action=racer.bulk&who=all&what=checkin"
round="$(roundid_of Snapshot 1)"
post_ok "action=schedule.generate&roundid=$round"
post_ok "action=heat.select&roundid=$round&now_racing=1"
# Gray: 3.0 and 3.8 s, average 3.4, best 3.0.  Hart: 3.2 and 3.3 s.
RACE_TIME_EXPR='({"401":[3000,3800],"402":[3200,3300]}[(.carnumber|tostring)][.lane - 1])' race_round

capture_scenario standings/q13-drop-slowest
capture "standings before the snapshot" "GET action.php?query=standings"
# The uploaded snapshot is a fixture too, so its clock values are fixed: the
# timer's last contact is left out, and every heat finished at one made-up time.
SNAPSHOT=standings/q13-drop-slowest.snapshot.xml
curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/action.php?query=snapshot.get" \
  | sed -e '1,/<!\[CDATA\[/d' -e '/^\]\]><\/snapshot>$/d' \
  | jq "$(cat <<'EOF'
map(if .table == "RaceInfo" then
      (.["high-water"] + 1) as $id
      | .rows |= (map(select(.itemkey | IN("scoring", "timer_state") | not))
                  + [{raceinfoid: $id, itemkey: "drop-slowest", itemvalue: "1"}])
      | .["high-water"] = $id
    elif .table == "RaceChart" then
      .rows |= map(if .completed then .completed = "2026-01-01 12:00:00" else . end)
    else . end)
EOF
)" > "$RAW/.snapshot.json"
{ printf '<?xml version="1.0" encoding="UTF-8"?>\n<snapshot><![CDATA[\n'
  cat "$RAW/.snapshot.json"
  printf ']]></snapshot>\n'; } > "$RAW/$SNAPSHOT"
CAPTURE_UPLOAD="snapshot=$SNAPSHOT" capture_ok snapshot.put "POST action.php action=snapshot.put"
capture "settings.list after the snapshot" "GET action.php?query=settings.list"
capture "standings after the snapshot" "GET action.php?query=standings"
capture "settings.list after standings" "GET action.php?query=settings.list"
