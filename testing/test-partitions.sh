#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

RESET_SOURCE=partitions1 `dirname $0`/reset-database.sh "$BASE_URL"
curl_postj action.php "action=racer.import&firstname=Kristie&lastname=Kyzer" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Shana&lastname=Sester" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Glendora&lastname=Giusti" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Ophelia&lastname=Oja" | check_jsuccess
curl_postj action.php "action=racer.import&firstname=Mirna&lastname=Manier" | check_jsuccess

curl_getj "action.php?query=poll&values=partitions" | \
    jq -e '.partitions | length == 1 and .[0].name == "Default"' >/dev/null || test_fails

RESET_SOURCE=partitions2 `dirname $0`/reset-database.sh "$BASE_URL"
`dirname $0`/import-csv-roster.sh "$BASE_URL" "`dirname $0`/data/divided-roster.csv"

# These IDs don't change when the groups are reordered or regrouped, and the
# checks below hold the groups to that
P_DEFAULT=$(partitionid_of Default)
P_DIV2=$(partitionid_of "Div 2")
P_DIV3=$(partitionid_of "Div 3")
C_DEFAULT=$(classid_of Default)
C_DIV2=$(classid_of "Div 2")
C_DIV3=$(classid_of "Div 3")
R_DEFAULT=$(rankid_of Default)
R_DIV2=$(rankid_of "Div 2")
R_DIV3=$(rankid_of "Div 3")

curl_getj "action.php?query=poll&values=partitions" | \
    jq -e '.partitions | length == 3 and 
                .[0].name == "Default" and 
                .[0].count == 20 and
                .[1].name == "Div 2" and
                .[1].count == 19 and
                .[2].name == "Div 3" and
                .[2].count == 10' >/dev/null || test_fails

curl_getj "action.php?query=poll&values=classes" | \
    jq -e --argjson c_default $C_DEFAULT --argjson r_default $R_DEFAULT --argjson c_div2 $C_DIV2 --argjson r_div2 $R_DIV2 --argjson c_div3 $C_DIV3 --argjson r_div3 $R_DIV3 '.classes | length == 3 
        and .[0].name == "Default" and .[0].classid == $c_default
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Default"
        and .[0].subgroups[0].rankid == $r_default
        and .[1].name == "Div 2" and .[1].classid == $c_div2
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 2"
        and .[1].subgroups[0].rankid == $r_div2
        and .[2].name == "Div 3" and .[2].classid == $c_div3
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 3"
        and .[2].subgroups[0].rankid == $r_div3
' >/dev/null || test_fails

# With by-partition rule, reordering classes should reorder partitions
curl_postj action.php "action=class.order&classid_1=$C_DIV2&classid_2=$C_DEFAULT&classid_3=$C_DIV3" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e --argjson c_div2 $C_DIV2 --argjson r_div2 $R_DIV2 --argjson c_default $C_DEFAULT --argjson r_default $R_DEFAULT --argjson c_div3 $C_DIV3 --argjson r_div3 $R_DIV3 '.classes | length == 3 
        and .[0].name == "Div 2" and .[0].classid == $c_div2
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"
        and .[0].subgroups[0].rankid == $r_div2
        and .[1].name == "Default" and .[1].classid == $c_default
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Default"
        and .[1].subgroups[0].rankid == $r_default
        and .[2].name == "Div 3" and .[2].classid == $c_div3
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 3"
        and .[2].subgroups[0].rankid == $r_div3
' >/dev/null || test_fails

curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_default $P_DEFAULT --argjson p_div3 $P_DIV3 '.partitions | length == 3 
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2 and .[0].count == 19
        and .[1].name == "Default" and .[1].partitionid == $p_default and .[1].count == 20
        and .[2].name == "Div 3" and .[2].partitionid == $p_div3 and .[2].count == 10
' >/dev/null || test_fails

curl_postj action.php "action=partition.apply-rule&rule=one-group" | check_jsuccess

# Each subgroup is the rank that the partition of the same name refers to.  The
# server may make new ranks when the rule changes, so the ranks' IDs are looked
# up again afterward.
curl_getj "action.php?query=poll&values=classes,partitions" | \
    jq -e '. as $d
        | def partrank(n): $d.partitions | map(select(.name == n))[0].rankids[0];
        .classes | length == 1
        and .[0].name == "All Racers" and .[0].count == 49 and (.[0].subgroups | length == 3)
        and .[0].subgroups[0].name == "Div 2"
        and .[0].subgroups[0].rankid == partrank("Div 2")
        and .[0].subgroups[0].count == 19
        and .[0].subgroups[1].name == "Default"
        and .[0].subgroups[1].rankid == partrank("Default")
        and .[0].subgroups[1].count == 20
        and .[0].subgroups[2].name == "Div 3"
        and .[0].subgroups[2].rankid == partrank("Div 3")
        and .[0].subgroups[2].count == 10
' >/dev/null || test_fails
R_DEFAULT=$(rankid_of Default)
R_DIV2=$(rankid_of "Div 2")
R_DIV3=$(rankid_of "Div 3")

# With one-group rule, reordering subdgroups should reorder partitions
curl_postj action.php "action=rank.order&rankid_1=$R_DEFAULT&rankid_2=$R_DIV2&rankid_3=$R_DIV3" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e --argjson r_default $R_DEFAULT --argjson r_div2 $R_DIV2 --argjson r_div3 $R_DIV3 '.classes | length == 1
        and .[0].name == "All Racers" and (.[0].subgroups | length == 3)
        and .[0].subgroups[0].name == "Default"
        and .[0].subgroups[0].rankid == $r_default
        and .[0].subgroups[1].name == "Div 2"
        and .[0].subgroups[1].rankid == $r_div2
        and .[0].subgroups[2].name == "Div 3"
        and .[0].subgroups[2].rankid == $r_div3
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_default $P_DEFAULT --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 '.partitions | length == 3 
        and .[0].name == "Default" and .[0].partitionid == $p_default
        and .[1].name == "Div 2" and .[1].partitionid == $p_div2
        and .[2].name == "Div 3" and .[2].partitionid == $p_div3
' >/dev/null || test_fails

curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess

curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 3 
        and .[0].name == "Default" and .[0].count == 20 and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Default"
        and .[1].name == "Div 2" and .[1].count == 19 and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 2"
        and .[2].name == "Div 3" and .[2].count == 10 and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 3"
' >/dev/null || test_fails

curl_postj action.php "action=partition.apply-rule&rule=custom" | check_jsuccess

curl_getj "action.php?query=poll&values=classes,partitions" | \
    jq -e '. as $d
        | def partrank(n): $d.partitions | map(select(.name == n))[0].rankids[0];
        .classes | length == 3
        and .[0].name == "Default" and .[0].count == 20 and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Default"
        and .[0].subgroups[0].count == 20
        and .[0].subgroups[0].rankid == partrank("Default")
        and .[1].name == "Div 2"
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 2"
        and .[1].subgroups[0].count == 19
        and .[1].subgroups[0].rankid == partrank("Div 2")
        and .[2].name == "Div 3"
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 3"
        and .[2].subgroups[0].count == 10
        and .[2].subgroups[0].rankid == partrank("Div 3")
' >/dev/null || test_fails

curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e '.partitions | length == 3
        and .[0].name == "Default"
        and .[1].name == "Div 2"
        and .[2].name == "Div 3"
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 3
        and .[0].name == "Default"
        and .[1].name == "Div 2"
        and .[2].name == "Div 3"
' >/dev/null || test_fails

curl_postj action.php "action=partition.order&partitionid_1=$P_DIV3&partitionid_2=$P_DIV2&partitionid_3=$P_DEFAULT" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_div3 $P_DIV3 --argjson p_div2 $P_DIV2 --argjson p_default $P_DEFAULT '.partitions | length == 3
        and .[0].name == "Div 3" and .[0].partitionid == $p_div3
        and .[1].name == "Div 2" and .[1].partitionid == $p_div2
        and .[2].name == "Default" and .[2].partitionid == $p_default
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 3
        and .[0].name == "Div 3"
        and .[1].name == "Div 2"
        and .[2].name == "Default"
' >/dev/null || test_fails

curl_postj action.php "action=partition.apply-rule&rule=one-group" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 1
        and .[0].name == "All Racers"
        and (.[0].subgroups | length == 3)
        and .[0].subgroups[0].name == "Div 3"
        and .[0].subgroups[1].name == "Div 2"
        and .[0].subgroups[2].name == "Default"
' >/dev/null || test_fails
curl_postj action.php "action=partition.order&partitionid_1=$P_DIV2&partitionid_2=$P_DIV3&partitionid_3=$P_DEFAULT" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT '.partitions | length == 3
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
        and .[2].name == "Default" and .[2].partitionid == $p_default
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 1
        and .[0].name == "All Racers"
        and (.[0].subgroups | length == 3)
        and .[0].subgroups[0].name == "Div 2"
        and .[0].subgroups[1].name == "Div 3"
        and .[0].subgroups[2].name == "Default"
' >/dev/null || test_fails

curl_postj action.php "action=partition.edit&partitionid=$P_DEFAULT&name=Div%202" | check_jfailure
curl_postj action.php "action=partition.edit&partitionid=$P_DEFAULT&name=Default" | check_jsuccess # no-op
curl_postj action.php "action=partition.edit&partitionid=$P_DEFAULT&name=Div%201" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions,classes" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT '
        . as $d
        | def class_of: .classids[0] as $c | $d.classes | map(select(.classid == $c))[0];
          def rank_of: .rankids[0] as $r | class_of | .subgroups | map(select(.rankid == $r))[0];
        .partitions | length == 3
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
            and (.[0] | class_of.name == "All Racers" and rank_of.name == "Div 2")
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
            and (.[1] | class_of.name == "All Racers" and rank_of.name == "Div 3")
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
            and (.[2] | class_of.name == "All Racers" and rank_of.name == "Div 1")
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 1
        and .[0].name == "All Racers"
        and (.[0].subgroups | length == 3)
        and .[0].subgroups[0].name == "Div 2"
        and .[0].subgroups[1].name == "Div 3"
        and .[0].subgroups[2].name == "Div 1"
' >/dev/null || test_fails

curl_postj action.php "action=partition.add&name=Div%201" | check_jfailure
curl_postj action.php "action=partition.add&name=New%20Div" | check_jsuccess
P_NEWDIV=$(partitionid_of "New Div")
curl_getj "action.php?query=poll&values=partitions,classes" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT --argjson p_newdiv $P_NEWDIV '
        . as $d
        | def class_of: .classids[0] as $c | $d.classes | map(select(.classid == $c))[0];
          def rank_of: .rankids[0] as $r | class_of | .subgroups | map(select(.rankid == $r))[0];
        .partitions | length == 4
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
            and (.[0] | class_of.name == "All Racers" and rank_of.name == "Div 2")
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
            and (.[1] | class_of.name == "All Racers" and rank_of.name == "Div 3")
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
            and (.[2] | class_of.name == "All Racers" and rank_of.name == "Div 1")
        and .[3].name == "New Div" and .[3].partitionid == $p_newdiv
            and (.[3] | class_of.name == "All Racers" and rank_of.name == "New Div")
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 1
        and .[0].name == "All Racers"
        and (.[0].subgroups | length == 4)
        and .[0].subgroups[0].name == "Div 2"
        and .[0].subgroups[1].name == "Div 3"
        and .[0].subgroups[2].name == "Div 1"
        and .[0].subgroups[3].name == "New Div"
' >/dev/null || test_fails


# 'by-partition', then 'custom'
curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions,classes" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT --argjson p_newdiv $P_NEWDIV '
        . as $d
        | def class_of: .classids[0] as $c | $d.classes | map(select(.classid == $c))[0];
          def rank_of: .rankids[0] as $r | class_of | .subgroups | map(select(.rankid == $r))[0];
        .partitions | length == 4
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
            and (.[0] | class_of.name == "Div 2" and rank_of.name == "Div 2")
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
            and (.[1] | class_of.name == "Div 3" and rank_of.name == "Div 3")
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
            and (.[2] | class_of.name == "Div 1" and rank_of.name == "Div 1")
        and .[3].name == "New Div" and .[3].partitionid == $p_newdiv
            and (.[3] | class_of.name == "New Div" and rank_of.name == "New Div")
' >/dev/null || test_fails
curl_postj action.php "action=partition.apply-rule&rule=custom" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions,classes" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT --argjson p_newdiv $P_NEWDIV '
        . as $d
        | def class_of: .classids[0] as $c | $d.classes | map(select(.classid == $c))[0];
          def rank_of: .rankids[0] as $r | class_of | .subgroups | map(select(.rankid == $r))[0];
        .partitions | length == 4
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
            and (.[0] | class_of.name == "Div 2" and rank_of.name == "Div 2")
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
            and (.[1] | class_of.name == "Div 3" and rank_of.name == "Div 3")
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
            and (.[2] | class_of.name == "Div 1" and rank_of.name == "Div 1")
        and .[3].name == "New Div" and .[3].partitionid == $p_newdiv
            and (.[3] | class_of.name == "New Div" and rank_of.name == "New Div")
' >/dev/null || test_fails

# Move partition 2 to the class already containing partition 1.  That should
# leave partition 2's original class empty, and so removed by cleanup.
curl_getj "action.php?query=poll&values=partitions" > /dev/null
P1CLASSID=$(jq ".partitions | map(select(.partitionid==$P_DEFAULT))[0].classids[0]" $DEBUG_CURL)
P2CLASSID=$(jq ".partitions | map(select(.partitionid==$P_DIV2))[0].classids[0]" $DEBUG_CURL)
curl_getj "action.php?query=poll&values=rounds" > /dev/null
P1ROSTERSIZE=$(jq ".rounds | map(select(.classid==$P1CLASSID))[0].roster_size" $DEBUG_CURL)
P2ROSTERSIZE=$(jq ".rounds | map(select(.classid==$P2CLASSID))[0].roster_size" $DEBUG_CURL)

curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess
curl_getj "action.php?query=poll&values=partitions" > /dev/null
# New classes makes potentially new classids for the partitions
P1CLASSID=$(jq ".partitions | map(select(.partitionid==$P_DEFAULT))[0].classids[0]" $DEBUG_CURL)
P2CLASSID=$(jq ".partitions | map(select(.partitionid==$P_DIV2))[0].classids[0]" $DEBUG_CURL)
# Confirm rosters are as expected
curl_getj "action.php?query=poll&values=rounds" | \
    jq -e ".rounds | map(select(.classid==$P1CLASSID))[0].roster_size == $P1ROSTERSIZE" \
       > /dev/null || test_fails
curl_getj "action.php?query=poll&values=rounds" | \
    jq -e ".rounds | map(select(.classid==$P2CLASSID))[0].roster_size == $P2ROSTERSIZE" \
       > /dev/null || test_fails

# Create a new empty partition
curl_postj action.php "action=partition.add&name=Empty" | check_jsuccess
P_EMPTY=$(partitionid_of Empty)
# Confirm there's a corresponding class, rank, round, and (empty) roster.
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT --argjson p_newdiv $P_NEWDIV --argjson p_empty $P_EMPTY '.partitions | length == 5
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
        and .[3].name == "New Div" and .[3].partitionid == $p_newdiv
        and .[4].name == "Empty" and .[4].partitionid == $p_empty
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 5
        and .[0].name == "Div 2"
        and .[0].count == 19
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"

        and .[1].name == "Div 3"
        and .[1].count == 10
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 3"

        and .[2].name == "Div 1"
        and .[2].count == 20
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 1"

        and .[3].name == "New Div"
        and .[3].count == 0
        and (.[3].subgroups | length == 1)
        and .[3].subgroups[0].name == "New Div"

        and .[4].name == "Empty"
        and .[4].count == 0
        and (.[4].subgroups | length == 1)
        and .[4].subgroups[0].name == "Empty"
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=rounds" | \
    jq -e '.rounds | length == 5
        and .[0].class == "Div 2"
        and .[0].round == 1
        and .[0].roster_size == 19
        and .[1].class == "Div 3"
        and .[1].round == 1
        and .[1].roster_size == 10
        and .[2].class== "Div 1"
        and .[2].round == 1
        and .[2].roster_size == 20
        and .[3].class == "New Div"
        and .[3].round == 1
        and .[3].roster_size == 0
        and .[4].class == "Empty"
        and .[4].round == 1
        and .[4].roster_size == 0
' >/dev/null || test_fails

# Re-apply by-partition rule with cleanup
curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess
# Confirm empty class, rank, round, and roster still present
curl_getj "action.php?query=poll&values=partitions" | \
    jq -e --argjson p_div2 $P_DIV2 --argjson p_div3 $P_DIV3 --argjson p_default $P_DEFAULT --argjson p_newdiv $P_NEWDIV --argjson p_empty $P_EMPTY '.partitions | length == 5
        and .[0].name == "Div 2" and .[0].partitionid == $p_div2
        and .[1].name == "Div 3" and .[1].partitionid == $p_div3
        and .[2].name == "Div 1" and .[2].partitionid == $p_default
        and .[3].name == "New Div" and .[3].partitionid == $p_newdiv
        and .[4].name == "Empty" and .[4].partitionid == $p_empty
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 5
        and .[0].name == "Div 2"
        and .[0].count == 19
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"
        and .[1].name == "Div 3"
        and .[1].count == 10
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 3"
        and .[2].name == "Div 1"
        and .[2].count == 20
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 1"
        and .[3].name == "New Div"
        and .[3].count == 0
        and (.[3].subgroups | length == 1)
        and .[3].subgroups[0].name == "New Div"
        and .[4].name == "Empty"
        and .[4].count == 0
        and (.[4].subgroups | length == 1)
        and .[4].subgroups[0].name == "Empty"
' >/dev/null || test_fails
curl_getj "action.php?query=poll&values=rounds" | \
    jq -e '.rounds | length == 5
        and .[0].class == "Div 2"
        and .[0].round == 1
        and .[0].roster_size == 19
        and .[1].class == "Div 3"
        and .[1].round == 1
        and .[1].roster_size == 10
        and .[2].class== "Div 1"
        and .[2].round == 1
        and .[2].roster_size == 20
        and .[3].class == "New Div"
        and .[3].round == 1
        and .[3].roster_size == 0
        and .[4].class == "Empty"
        and .[4].round == 1
        and .[4].roster_size == 0
' >/dev/null || test_fails

# apply-rule by-partition, then custom
# Move a rank to an existing class
# Move another rank to -1 from a like-named class
curl_postj action.php "action=partition.apply-rule&rule=by-partition" | check_jsuccess
curl_postj action.php "action=partition.apply-rule&rule=custom" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 5
        and .[0].name == "Div 2"
        and .[0].count == 19
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"
        and .[1].name == "Div 3"
        and .[1].count == 10
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 3"
        and .[2].name == "Div 1"
        and .[2].count == 20
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 1"
        and .[3].name == "New Div"
        and .[3].count == 0
        and (.[3].subgroups | length == 1)
        and .[3].subgroups[0].name == "New Div"
        and .[4].name == "Empty"
        and .[4].count == 0
        and (.[4].subgroups | length == 1)
        and .[4].subgroups[0].name == "Empty"
' >/dev/null || test_fails

# Move rankid 4 ("Empty") into classid 4 ("New Div"), so 
curl_postj action.php "action=rank.move&rankid=$(rankid_of Empty)&classid=$(classid_of "New Div")" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 4
        and .[0].name == "Div 2"
        and .[0].count == 19
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"

        and .[1].name == "Div 3"
        and .[1].count == 10
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 3"

        and .[2].name == "Div 1"
        and .[2].count == 20
        and (.[2].subgroups | length == 1)
        and .[2].subgroups[0].name == "Div 1"

        and .[3].name == "New Div"
        and .[3].count == 0
        and (.[3].subgroups | length == 2)
        and .[3].subgroups[0].name == "New Div"
        and .[3].subgroups[1].name == "Empty"
' >/dev/null || test_fails

curl_postj action.php "action=rank.move&rankid=$(rankid_of "Div 3")&classid=-1" | check_jsuccess
curl_getj "action.php?query=poll&values=classes" | \
    jq -e '.classes | length == 4
        and .[0].name == "Div 2"
        and .[0].count == 19
        and (.[0].subgroups | length == 1)
        and .[0].subgroups[0].name == "Div 2"

        and .[1].name == "Div 1"
        and .[1].count == 20
        and (.[1].subgroups | length == 1)
        and .[1].subgroups[0].name == "Div 1"

        and .[2].name == "New Div"
        and .[2].count == 0
        and (.[2].subgroups | length == 2)
        and .[2].subgroups[0].name == "New Div"
        and .[2].subgroups[1].name == "Empty"

        and .[3].name == "Div 3-1"
        and .[3].count == 10
        and (.[3].subgroups | length == 1)
        and .[3].subgroups[0].name == "Div 3"

' >/dev/null || test_fails

# Move all the "Div 3" racers to "New Div", then delete "Div 3"
for CARNO in 105 110 115 120 125 130 135 140 145 149; do
    curl_postj action.php "action=racer.edit&racerid=$(racerid_of $CARNO)&partitionid=$P_NEWDIV" | check_jsuccess
done
curl_postj action.php "action=partition.delete&partitionid=$P_DIV3" | check_jsuccess

curl_getj "action.php?query=database.check" | check_jsuccess
