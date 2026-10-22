# The role.login family that issue #16 ports: success with a password, the
# empty Timer password, a wrong password, an unknown role, logout, role.list and
# the XML stubs for old timer jars.

rm -f "$COOKIES_CURL"

capture_scenario login/role-login
capture role-list-anonymous "GET action.php?query=role.list"
capture login-coordinator "POST action.php action=role.login&name=RaceCoordinator&password=doyourbest"
capture role-list-as-coordinator "GET action.php?query=role.list"
capture login-wrong-password "POST action.php action=role.login&name=RaceCoordinator&password=wrong"
capture login-timer-empty-password "POST action.php action=role.login&name=Timer&password="
capture login-timer-no-password-field "POST action.php action=role.login&name=Timer"
capture login-unknown-role "POST action.php action=role.login&name=Nobody&password=x"
capture login-crew "POST action.php action=role.login&name=RaceCrew&password=murphy"
capture login-photo "POST action.php action=role.login&name=Photo&password=flashbulb"
capture logout "POST action.php action=role.login"
capture login-no-name-field "POST action.php action=role.login&password=doyourbest"
capture role-list-after-logout "GET action.php?query=role.list"
capture login-stub-xml "POST action.php action=login&name=RaceCoordinator&password=doyourbest"
capture roles-stub-xml "GET action.php?query=roles"
