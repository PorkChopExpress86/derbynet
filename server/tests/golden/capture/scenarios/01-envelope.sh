# What action.php itself writes, whatever handler would run: the dispatcher cases
# that issue #18 lists, plus a few neighbours.  All anonymous.

rm -f "$COOKIES_CURL"

capture_scenario envelope/dispatcher
capture not-a-page "GET action.php"
capture unknown-query "GET action.php?query=no.such"
capture unknown-action-encoding-quirks "POST action.php action=no.such&password=x&n=007&f=1.50&e=1e3&w=%205&h=0x1A&s=a/b&u=%C3%A9&message[recipient]=AA&message[key]=v&list[]=1&list[]=2&a.b=1"
capture unknown-xml-action-escaping "POST action.php action=snapshot.no-such&q=%27%22%3C%3E%26"
capture unknown-xml-query "GET action.php?query=snapshot.no-such"
capture post-with-query-string "POST action.php?query=poll action=no.such"

# Neighbours of the cases above
capture get-with-action-parameter "GET action.php?action=no.such"
capture post-with-empty-body "POST action.php"
capture query-name-empty "GET action.php?query="
capture action-name-empty "POST action.php action="
capture unknown-action-nodata "POST action.php action=no.such.nodata"
capture unknown-xml-action-login-stub "POST action.php action=login"
capture unknown-xml-query-roles-stub "GET action.php?query=roles"
capture xml-action-timer-message-without-login "POST action.php action=timer-message&message=HELLO"
capture query-with-array-parameter "GET action.php?query[]=poll"
capture post-with-both-same-key "POST action.php?action=role.login action=no.such"

# JSON refusals, anonymous: the common notauthorized code, and the permission
# code that class.add, class.edit, class.order, rank.add and rank.move use.
capture json-notauthorized "POST action.php action=kiosk.reload&address=x"
capture json-permission-failure "POST action.php action=class.edit&classid=1&name=x"
