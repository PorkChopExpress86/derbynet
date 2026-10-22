# The shell suite's own scripts, run unmodified through the recording proxy in
# the order exercise-one-database.sh runs them (leaving out the Puppeteer tests
# and the photo tests, which touch no state these scripts read).  The proxy
# records, per script:
#
#   finalists and concerns   the roster.new and playlist.* exchanges of the
#                            scripts that pick top finishers, for issue #26
#   state per step           classes, ranks, partitions, rounds and roster
#                            membership by name after each step of the group
#                            scripts, for issue #25
#   replay commands          the replay.message exchanges, for issues #27 and #34
#   reschedule               schedule.reschedule, where the search ends before
#                            its deadline
#
# After the scripts that leave results behind, the standings and awards are
# captured under every scoring setting.

FINALISTS="roster.new,roster.delete,playlist.add,playlist.start,playlist.delete,playlist.edit,playlist.order"

use_session coord
user_login_coordinator
use_session timer
user_login_timer
use_session coord

# ----- Basic racing (exercise-one-database.sh: ex0) -----
reset_database
run_script import-roster.sh
record_script suite/den-changes-basic "" 1 test-den-changes.sh
run_script test-each-role.sh
# test-basic-racing.sh expects a replay client that polled within the last ten
# seconds, and test-each-role.sh made the last such poll.  Poll again now, so a
# slow machine does not push the expected "HELLO" into the next command list.
curl_postj action.php "action=replay.message&status=-1&finished-replay=0" >/dev/null
record_script suite/basic-racing "replay.message,heat.rerun,heat.reinstate" 0 test-basic-racing.sh
run_script test-awards.sh basic
standings_matrix standings/suite-basic-racing-and-awards
record_script suite/new-rounds-basic "$FINALISTS" 0 test-new-rounds.sh
standings_matrix standings/suite-basic-racing-after-new-rounds

# ----- Late entrants and departed racers -----
record_script suite/reschedule "schedule.reschedule" 0 test-reschedule.sh

# ----- Standings by rank -----
record_script suite/standing-by-rank "" 0 test-standing-by-rank.sh
standings_matrix standings/suite-standing-by-rank
record_script suite/subgroup-ntrophies "" 0 test-subgroup-ntrophies.sh
standings_matrix standings/suite-subgroup-ntrophies

# ----- Points racing -----
record_script suite/points-racing "" 0 test-points-racing.sh
standings_matrix standings/suite-points-racing

# ----- Rounds playlist -----
record_script suite/playlist "$FINALISTS,replay.message" 0 test-playlist.sh
standings_matrix standings/suite-playlist

# ----- Racing groups and partitions (ex1) -----
reset_database
record_script suite/partitions "" 1 test-partitions.sh
record_script suite/racing-groups "" 1 test-racing-groups.sh
record_script suite/single-group-operation "" 1 setup-single-group-operation.sh

# ----- Master schedule (ex2) -----
reset_database
run_script import-roster.sh
run_script test-den-changes.sh
record_script suite/master-schedule "$FINALISTS" 0 test-master-schedule.sh
run_script test-awards.sh master
standings_matrix standings/suite-master-schedule
record_script suite/new-rounds-master "$FINALISTS" 0 test-new-rounds.sh
record_script suite/aggregate-rounds "$FINALISTS" 0 test-aggregate-rounds.sh
standings_matrix standings/suite-aggregate-rounds
record_script suite/aggregate-classes "$FINALISTS" 0 test-aggregate-classes.sh
standings_matrix standings/suite-aggregate-classes
record_script suite/aggregate-nonracing "$FINALISTS" 0 test-aggregate-nonracing.sh
standings_matrix standings/suite-aggregate-nonracing
