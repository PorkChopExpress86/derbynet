# The server starts with no database.  Record what it says then, and the setup
# action that creates one, which is how every later scenario gets its database.

capture_scenario envelope/before-setup
capture not-a-page "GET action.php"
capture poll-without-database "GET action.php?query=poll.coordinator"
capture login-without-database "POST action.php action=role.login&name=RaceCoordinator&password=doyourbest"
capture unrecognized-without-database "GET action.php?query=no.such"
capture nodata-query-without-database "GET action.php?query=no.such.nodata"

capture_scenario setup/ez-new
capture ez-new "POST action.php action=setup.nodata&ez-new=testdb"
user_login_coordinator
capture poll-after-setup "GET action.php?query=poll.coordinator"
capture settings-list-after-setup "GET action.php?query=settings.list"
