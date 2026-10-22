#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=aggregate-classes `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-roster.sh "$BASE_URL"

`dirname $0`/import-csv-roster.sh  "$BASE_URL" "`dirname $0`/data/outlaw-class.csv"

curl_postj action.php "action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox" | check_jsuccess
curl_postj action.php "action=rank.edit&rankid=$(rankid_of Outlaw)&name=Siblings" | check_jsuccess
curl_postj action.php "action=rank.add&classid=$(classid_of Outlaw)&name=Parents" | check_jsuccess
curl_postj action.php "action=rank.add&classid=$(classid_of Outlaw)&name=Desperados" | check_jsuccess

# Sib 6
# P   7
# Des 8

# Move to parents(rankid=7)
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Belle Starr")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Pearl Hart")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Sam Bass")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Cherokee Bill")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Fred Waite")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "Hoodoo Brown")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Parents)&racer=$(racerid_named "John Wesley Hardin")" | check_jsuccess

# Move to Desperado(rankid=8)
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Desperados)&racer=$(racerid_named "Doc Holliday")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Desperados)&racer=$(racerid_named "Butch Cassidy")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Desperados)&racer=$(racerid_named "Jesse James")" | check_jsuccess
curl_postj action.php "action=racer.edit&rankid=$(rankid_of Desperados)&racer=$(racerid_named "Zip Wyatt")" | check_jsuccess

`dirname $0`/run-scout-heats.sh "$BASE_URL"

curl_postj action.php "action=schedule.generate&roundid=$(roundid_of Outlaw 1)" | check_jsuccess

# Outlaw round: these are intentionally much faster
ROUND=$(roundid_of Outlaw 1)
curl_postj action.php "action=heat.select&roundid=$ROUND&now_racing=1" | check_jsuccess
run_heat $ROUND 1   9902:2.488 9907:2.656 9927:2.360 9922:2.518
run_heat $ROUND 2   9932:2.393 9942:2.273 9962:2.346 9957:2.293
run_heat $ROUND 3   9907:2.339 9912:2.698 9932:2.844 9927:2.465
run_heat $ROUND 4   9937:2.260 9947:2.351 9902:2.441 9962:2.672
run_heat $ROUND 5   9907:2.738 9917:2.392 9937:2.717 9932:2.682
run_heat $ROUND 6   9942:2.116 9952:2.591 9907:2.553 9902:2.800
run_heat $ROUND 7   9912:2.026 9922:2.381 9942:2.464 9937:2.843
run_heat $ROUND 8   9947:3.233 9957:3.127 9907:3.630 9907:3.694
run_heat $ROUND 9   9917:3.845 9927:3.144 9947:3.859 9942:3.090
run_heat $ROUND 10  9952:3.345 9962:3.515 9912:3.708 9907:3.150
run_heat $ROUND 11  9922:3.448 9932:3.127 9952:3.407 9947:3.005
run_heat $ROUND 12  9957:3.429 9902:3.329 9917:3.638 9912:3.074
run_heat $ROUND 13  9927:3.370 9937:3.475 9957:3.232 9952:3.869
run_heat $ROUND 14  9962:3.819 9907:3.065 9922:3.761 9917:3.551 x

# P   Belle Starr(92)
# P   Pearl Hart(90)
# Des Doc Holliday(83)
# Des Butch Cassidy(91)
# Sib Sundance Kid(89)
# P   Sam Bass(86)
# P   Cherokee Bill(84)
# Sib Apache Kid(95)
# P   Fred Waite(88)
# P   Hoodoo Brown(85)
# Sib Billy Kid(96)
# P   John Wesley Hardin(93)
# Des Jesse James(94)
# Des Zip Wyatt(87)

## Create "Younger" aggregate class of classes 1,2
curl_postj action.php "action=class.add&constituent_$(classid_of Lions)=1&constituent_$(classid_of White)=1&name=Younger" | check_jsuccess

## Create "Older" aggregate class of classes 3,4,5
curl_postj action.php "action=class.add&constituent_$(classid_of Bears)=1&constituent_$(classid_of Webelos)=1&constituent_$(classid_of Arrows)=1&name=Older" | check_jsuccess

## Create an aggregate of aggregates, "Scouts", that excludes Outlaw
curl_postj action.php "action=class.add&constituent_$(classid_of Younger)=1&constituent_$(classid_of Older)=1&name=Scouts" | check_jsuccess

## Create an aggregate of subgroups, "Firsts and Families", that includes
## the Lions & Tigers, Siblings and Parents subgroups
curl_postj action.php "action=class.add&rankid_$(rankid_of Lions)=1&rankid_$(rankid_of Siblings)=1&rankid_$(rankid_of Parents)=1&name=Firsts%20and%20Families" \
    | check_jsuccess

EXPECTED=$(cat <<'EOF'
["Edgardo Easterwood", "Pat Petrone", "Adolfo \"Dolf\" Asher", "Felton Fouche", "Carroll Cybulski", "Ben Bittinger", "Danial Depaolo", "Kelvin Knapp", "Darrell & Darrell Delaughter", "Levi Lahr", "Tracey Trapp", "Lewis Levitsky", "Kelvin Kinman", "Owen O'Leary", "Michal Melendrez", "Angelo Alas", "Willard Woolfolk", "Ian Ives", "Blake Burling", "Freddie Font", "Toby Teed", "Mohamed McGrew", "Raymon Ruffner", "Jesse Jara", "Josh Jose", "Dexter Dawes", "Renaldo Raposo", "Christoper Chauncey", "Herb Halfacre", "Royce Rohman", "Elliot Eastman", "Rodrigo Rencher", "Willard Wile", "Derick Dreier"]
EOF
)
standings_sheet "Younger" | jq -e --argjson e "$EXPECTED" 'map(.Name) == $e' >/dev/null || \
    test_fails Younger class rankings by export-json

EXPECTED=$(cat <<'EOF'
["Rex Rosalez", "Derek Dantonio", "Denny Deering", "Nelson No", "Sean Strasburg", "Pete Pinkney", "Julian Jarrard", "Darrin Denny", "Lanny Lavigne", "Travis Toothaker", "Byron Billy", "Freeman Fizer", "Harley Howell", "Judson Joynt", "Jamison Jeffress", "Jackson Juliano", "Norbert Nightingale", "Earnest Evangelista", "Emory Ertel", "Barney Bainter", "Cletus Creager", "Ethan Enye", "Robbie Roush", "Gregg Grove", "Bruce Boissonneault", "Markus Muncy", "Vincent Vinci", "Weston Whigham", "Dorian Dunkle", "Arden Aziz", "Jed Jaquez", "Kris Kaba", "Philip Prum", "Marlon McGray", "Numbers Nish", "Juan Jacobsen", "Reuben Rockhill", "Porter Papke", "Enoch Eccles", "Domingo Doles", "Lyman Liller", "Carey Craney", "Scottie Servais", "Timmy Tomei", "Clark Chesnutt", "Kory Kilgo", "Antoine Akiyama", "Cañumil Calero"]
EOF
)
standings_sheet "Older" | jq -e --argjson e "$EXPECTED" 'map(.Name) == $e' >/dev/null || \
    test_fails Older class rankings by export-json

EXPECTED=$(cat <<'EOF'
["Rex Rosalez", "Derek Dantonio", "Denny Deering", "Edgardo Easterwood", "Pat Petrone", "Nelson No", "Adolfo \"Dolf\" Asher", "Sean Strasburg", "Pete Pinkney", "Felton Fouche", "Carroll Cybulski", "Julian Jarrard", "Darrin Denny", "Ben Bittinger", "Danial Depaolo", "Kelvin Knapp", "Lanny Lavigne", "Travis Toothaker", "Darrell & Darrell Delaughter", "Levi Lahr", "Tracey Trapp", "Byron Billy", "Freeman Fizer", "Harley Howell", "Lewis Levitsky", "Judson Joynt", "Jamison Jeffress", "Jackson Juliano", "Norbert Nightingale", "Earnest Evangelista", "Emory Ertel", "Kelvin Kinman", "Barney Bainter", "Cletus Creager", "Ethan Enye", "Robbie Roush", "Owen O'Leary", "Gregg Grove", "Bruce Boissonneault", "Markus Muncy", "Vincent Vinci", "Michal Melendrez", "Weston Whigham", "Dorian Dunkle", "Arden Aziz", "Jed Jaquez", "Kris Kaba", "Angelo Alas", "Willard Woolfolk", "Ian Ives", "Philip Prum", "Marlon McGray", "Blake Burling", "Freddie Font", "Numbers Nish", "Toby Teed", "Mohamed McGrew", "Juan Jacobsen", "Reuben Rockhill", "Raymon Ruffner", "Porter Papke", "Jesse Jara", "Enoch Eccles", "Josh Jose", "Dexter Dawes", "Domingo Doles", "Renaldo Raposo", "Lyman Liller", "Christoper Chauncey", "Carey Craney", "Herb Halfacre", "Scottie Servais", "Royce Rohman", "Elliot Eastman", "Timmy Tomei", "Rodrigo Rencher", "Clark Chesnutt", "Kory Kilgo", "Antoine Akiyama", "Cañumil Calero", "Willard Wile", "Derick Dreier"]
EOF
)
standings_sheet "Scouts" | jq -e --argjson e "$EXPECTED" 'map(.Name) == $e' >/dev/null || \
    test_fails Scouts class rankings by export-json

EXPECTED=$(cat <<'EOF'
["Belle Starr", "Pearl Hart", "Sundance Kid", "Sam Bass", "Cherokee Bill", "Apache Kid", "Fred Waite", "Hoodoo Brown", "Billy Kid", "John Wesley Hardin", "Edgardo Easterwood", "Adolfo \"Dolf\" Asher", "Felton Fouche", "Carroll Cybulski", "Ben Bittinger", "Danial Depaolo", "Levi Lahr", "Kelvin Kinman", "Owen O'Leary", "Michal Melendrez", "Toby Teed", "Raymon Ruffner", "Jesse Jara", "Herb Halfacre", "Rodrigo Rencher", "Willard Wile", "Derick Dreier"]
EOF
)
standings_sheet "Firsts and Families" | jq -e --argjson e "$EXPECTED" 'map(.Name) == $e' >/dev/null || \
    test_fails Firsts-and-Families class rankings by export-json

# Test generating a round from an aggregate class by subgroup
RANK_FINAL=`mktemp`
curl_postj action.php "action=roster.new&classid=$(classid_of "Firsts and Families")&top=2&bucketed=1" | tee $RANK_FINAL | check_jsuccess
finalists_carnumbers < $RANK_FINAL | expect_eq '[101,126,9927,9932,9942,9957]'
