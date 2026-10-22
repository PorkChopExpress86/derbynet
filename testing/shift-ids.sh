#! /bin/bash
#
# Shifts the IDs that a database hands out next.  It creates and deletes 10
# throwaway racers, each with a group of its own (and the class, rank, partition
# and round that come with one), and 10 throwaway awards.  Whatever the suite
# creates afterwards gets an ID that is 10 higher than on a fresh database, so a
# test that depends on a particular ID fails.
#
# This only shifts anything on a server that never reuses an ID, as Django's
# SQLite backend doesn't.  SQLite doesn't unless the table declares AUTOINCREMENT:
# see the "autoincrement" step of docker/run-suite.sh.
#
# reset-database.sh runs this when DERBYNET_SHIFT_IDS is set.  The caller is
# logged in as the coordinator.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

for I in 1 2 3 4 5 6 7 8 9 10 ; do
    RACERID=$(curl_postj action.php \
                  "action=racer.import&firstname=Throwaway&lastname=Racer-$I&partition=Throwaway-$I" \
                  | jq -e .racerid)
    curl_postj action.php "action=award.import&awardname=Throwaway-$I&awardtype=Design%20Trophy" | check_jsuccess
    PARTITIONID=$(partitionid_of Throwaway-$I)
    AWARDID=$(awardid_of Throwaway-$I)

    curl_postj action.php "action=racer.delete&racer=$RACERID" | check_jsuccess
    curl_postj action.php "action=partition.delete&partitionid=$PARTITIONID" | check_jsuccess
    curl_postj action.php "action=award.delete&awardid=$AWARDID" | check_jsuccess
done

# A server that reuses the ID of a deleted row gave every throwaway the same ID,
# and nothing shifted
[ "$RACERID" -ge 10 ] && [ "$PARTITIONID" -ge 10 ] && [ "$AWARDID" -ge 10 ] || \
    test_fails "IDs didn't shift: the last throwaway got racerid $RACERID," \
               "partitionid $PARTITIONID, awardid $AWARDID"

# Nothing of the throwaways is left
curl_getj "action.php?query=poll&values=partitions,classes,rounds" | \
    jq -e '.partitions == [] and .classes == [] and .rounds == []' >/dev/null || test_fails
curl_getj "action.php?query=racer.list" | jq -e '.racers == []' >/dev/null || test_fails
curl_getj "action.php?query=award.list" | jq -e '.awards == []' >/dev/null || test_fails
