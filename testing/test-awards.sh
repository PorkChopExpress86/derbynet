#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

if [ "$2" = "basic" ] ; then
    # Basic racing
    SECOND_TEST="Kris Kaba"
    THIRD_TEST="Blake Burling"
else
    # Master schedule racing
    SECOND_TEST="Ben Bittinger"
    THIRD_TEST="Herb Halfacre"
fi

# This test depends on the set-up and simulated racing from other tests.

user_login_coordinator

curl_postj action.php "action=award.import" | check_jfailure
curl_postj action.php "action=award.import&awardname=Test%20Award" | check_jfailure
# curl_postj action.php "action=award.import&awardname=Test%20Award&awardtype=Design" | check_jfailure
curl_postj action.php "action=award.import&awardname=Test%20Award&awardtype=Design%20Trophy" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Test%20Den%20Award&classname=Hyenas&awardtype=Design%20Trophy" | check_jfailure
curl_postj action.php "action=award.import&awardname=Test%20Den%20Award&classname=White's%20Wolves&awardtype=Design%20Trophy" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Test%20Rank%20Award&subgroup=White's%20Wolves&awardtype=Design%20Trophy&racerid=$(racerid_of 435)" | check_jsuccess
curl_postj action.php "action=award.import&awardname=Test%20Pack%20Award&awardtype=Design%20Trophy&carnumber=222&sort=6" | check_jsuccess

curl_postj action.php "action=award.present&key=award-$(awardid_of "Test Rank Award")" | check_jsuccess
curl_getj "action.php?query=award.current" | \
    jq '(.current.reveal | not) and (.current.recipient | test("Howell.*"))' | expect_eq true

curl_getj "action.php?query=award.list" | jq '.awards | length' | expect_eq 4
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 435) \
       '.awards | map(select(.awardname == "Test Rank Award")) | length == 1 and .[0].racerid == $r' | \
    expect_eq true
curl_getj "action.php?query=award.list" | \
    jq '.awards | map(select(.awardtypeid == 4)) | length' | \
    expect_eq 4
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 222) \
       '.awards | map(select(.awardname == "Test Pack Award")) | .[] | .racerid == $r' | \
    expect_eq true

curl_postj action.php "action=racer.edit&racer=$(racerid_of 111)&firstname=Carroll&lastname=Cybulski&carno=111&carname=Vroom&rankid=$(rankid_of Lions)&exclude=1" | check_jsuccess
curl_postj action.php "action=award.winner&awardid=$(awardid_of "Test Award")&racerid=$(racerid_of 111)" | check_jfailure
curl_postj action.php "action=award.winner&awardid=$(awardid_of "Test Award")&racerid=$(racerid_of 202)" | check_jsuccess
curl_postj action.php "action=award.winner&awardid=$(awardid_of "Test Award")&racerid=0" | check_jsuccess
curl_postj action.php "action=award.winner&awardid=$(awardid_of "Test Award")&racerid=$(racerid_of 202)" | check_jsuccess

curl_postj action.php "action=award.edit&awardid=$(awardid_of "Test Den Award")&class_and_rank=$(classid_of Bears),$(rankid_of Bears)" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Test Rank Award")&name=Third" | check_jsuccess
curl_postj action.php "action=award.edit&awardid=$(awardid_of "Test Pack Award")&awardtypeid=2" | check_jsuccess

curl_postj action.php "action=award.winner&awardid=$(awardid_of "Test Den Award")&racerid=$(racerid_of 202)" | check_jfailure

curl_getj "action.php?query=award.list" | jq '.awards | length' | expect_eq 4
curl_getj "action.php?query=award.list" | \
    jq --argjson c $(classid_of Bears) '.awards | map(select(.classid == $c)) | length' | expect_eq 1
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(rankid_of Bears) '.awards | map(select(.rankid == $r)) | length' | expect_eq 1
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 202) \
       '.awards | map(select(.awardname == "Test Award")) |
        length == 1 and .[0].racerid == $r' | \
    expect_eq true
curl_getj "action.php?query=award.list" | \
    jq --argjson c $(classid_of Bears) --argjson k $(rankid_of Bears) \
       '.awards | map(select(.awardname == "Test Den Award")) |
        length == 1 and .[0].classid == $c and .[0].rankid == $k' | \
    expect_eq true
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 435) \
       '.awards | map(select(.awardname == "Third")) |
        length == 1 and .[0].racerid == $r' | \
    expect_eq true
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 222) \
       '.awards | map(select(.awardname == "Test Pack Award")) |
        length == 1 and .[0].racerid == $r and .[0].awardtypeid == 2' | \
    expect_eq true

curl_postj action.php "action=award.edit&awardid=$(awardid_of "Test Den Award")&class_and_rank=0,0" | check_jsuccess
curl_getj "action.php?query=award.list" | \
    jq '.awards | map(select(.awardname == "Test Den Award")) | length == 1 and .[0].classid == 0' | \
    expect_eq true

curl_postj action.php "action=award.delete&awardid=$(awardid_of Third)" | \
    jq '.awards | length' | expect_eq 3
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=4&name=NewThird" | \
    jq '.awards | length' | expect_eq 4
curl_getj "action.php?query=award.list" | \
    jq '.awards | map(select(.awardname == "NewThird")) | length == 1 and .[0].sort == 7' | \
    expect_eq true
curl_postj action.php "action=award.order&awardid_1=$(awardid_of "Test Pack Award")&awardid_2=$(awardid_of NewThird)&awardid_3=$(awardid_of "Test Den Award")&awardid_4=$(awardid_of "Test Award")" | \
    jq '.awards | map(select(.awardname == "Test Pack Award")) | length == 1 and .[0].sort == 1' | \
    expect_eq true

curl_getj "action.php?query=award.list" | \
    jq '.awards | all((.awardname == "Test Pack Award" and .sort == 1) or
                      (.awardname == "NewThird" and .sort == 2) or
                      (.awardname == "Test Den Award" and .sort == 3) or
                      (.awardname == "Test Award" and .sort == 4))' | \
                          expect_eq true

## Create "Younger" aggregate class of the first two classes
curl_postj action.php "action=class.add&constituent_$(classid_of Lions)=1&constituent_$(classid_of White)=1&name=Younger" | check_jsuccess
## Create an award for the aggregate class.  Award rankid won't matter for an aggregate class award.
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=2&name=AggClass%20Award&class_and_rank=$(classid_of Younger),0" | check_jsuccess

curl_postj action.php "action=award.winner&awardid=$(awardid_of "AggClass Award")&racerid=$(racerid_of 348)" | check_jfailure
curl_postj action.php "action=award.winner&awardid=$(awardid_of "AggClass Award")&racerid=$(racerid_of 101)" | check_jsuccess

## Create an empty subgroup for Lions & Tigers
curl_postj action.php "action=settings.write&do-use-subgroups=1&do-use-subgroups-checkbox=1" | check_jsuccess
curl_postj action.php "action=rank.add&name=Private&classid=$(classid_of Lions)" | check_jsuccess
## Create an "aggregate" class comprising only the new Private rankid
curl_postj action.php "action=class.add&rankid_$(rankid_of Private)=1&name=AggExclusive" | check_jsuccess
## Create an award for that exclusive (empty) class
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=2&name=Exclusive&class_and_rank=$(classid_of Lions),$(rankid_of Private)" | check_jsuccess

curl_postj action.php "action=award.winner&awardid=$(awardid_of Exclusive)&racerid=$(racerid_of 101)" | check_jfailure
# Move racer 1 to the previously empty subgroup and now the award can be granted
curl_postj action.php "action=racer.edit&racer=$(racerid_of 101)&rankid=$(rankid_of Private)" | check_jsuccess
curl_postj action.php "action=award.winner&awardid=$(awardid_of Exclusive)&racerid=$(racerid_of 101)" | check_jsuccess

# Try some ad-hoc awards
# 21 = Derek Dreier, car 121
# 12 = Christopher Chauncey, car 212
curl_postj action.php "action=award.adhoc&racerid=$(racerid_of 121)&awardname=Best%20Use%20Of%20Chocolate" | check_jsuccess
curl_postj action.php "action=award.adhoc&racerid=$(racerid_of 212)&awardname=Most%20Glittery" | check_jsuccess
curl_postj action.php "action=award.adhoc&racerid=$(racerid_of 121)&awardname=" | check_jsuccess

curl_getj "action.php?query=award.list" | expect_count 'Chocolate' 0
curl_getj "action.php?query=award.list" | \
    jq --argjson r $(racerid_of 212) \
       '.awards | map(select(.racerid == $r)) | length == 1 and .[0].awardname == "Most Glittery" and .[0].adhoc' | \
    expect_eq true

# Ad hoc awards aren't supposed to affect numbering for named awards
curl_postj action.php "action=award.edit&awardid=new&awardtypeid=4&name=NewFourth" | check_jsuccess
curl_getj "action.php?query=award.list" | \
    jq '.awards | map(select(.awardname == "NewFourth")) | length == 1 and .[0].sort == 7' | \
    expect_eq true

# There's no current award at the very beginning, but we don't want to enforce
# that this test has to be run at the very beginning.
#
# curl_getj "action.php?query=award.current" | check_jfailure

# The presence of two aggregate classes would make second-fastest-in-pack (speed-2)
# not meaningful.
curl_postj action.php "action=class.delete&classid=$(classid_of AggExclusive)" | check_jsuccess
curl_postj action.php "action=class.delete&classid=$(classid_of Younger)" | check_jsuccess

curl_postj action.php "action=award.present&key=speed-2" | check_jsuccess
curl_getj "action.php?query=award.current" | \
    jq '.current.reveal' | expect_eq false

curl_postj action.php "action=award.present&reveal=1" | check_jsuccess
curl_getj "action.php?query=award.current" | \
    jq --arg R "$SECOND_TEST" '.current.reveal and .current.recipient == $R' | \
    expect_eq true

curl_postj action.php "action=award.present&reveal=0&key=speed-3" | check_jsuccess
curl_getj "action.php?query=award.current" | \
    jq --arg R "$THIRD_TEST" '(.current.reveal | not) and .current.recipient == $R' | \
    expect_eq true
