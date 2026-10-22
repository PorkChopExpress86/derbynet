#! /bin/bash
#
# Fails when a test script contains a literal database ID.
#
# The suite has to pass against a server that assigns IDs differently from the
# PHP server (ADR-12), so a script asks for an ID by a stable key (racerid_of,
# roundid_of, classid_of, ...) or takes it from the response that created the
# thing, and never writes 5 where it means "the racer I imported fifth".
#
# What counts as a literal ID, in the scripts the suite runs:
#   - a parameter that names an ID and gets a number: racer=5, roundid=1,
#     classid_3=1, constituent_7=1
#   - the number in an award key that names a group: speed-1-4, award-3
#   - run_heat's first argument, which is a roundid
#   - jq comparing an ID field with a number: .roundid == 3, or a golden
#     response with "classid": 1 in it
#   - an ID list: votes=[12,36], classids=2,3, class_and_rank=3,3
# 0 and -1 are not IDs: they mean "none" and "invalid", and are allowed.  Neither
# are sceneid and awardtypeid: the server starts with a fixed set of scenes and
# award types, and no query gives their IDs.
#
# A line that needs a literal ID anyway goes in check-no-literal-ids.allowlist,
# with the file, a piece of the line, and the reason.  The check also fails for
# an entry that no longer matches anything.
#
# Usage: testing/check-no-literal-ids.sh [file ...]

cd "$(dirname "$0")"
ALLOWLIST=check-no-literal-ids.allowlist

if [ $# -gt 0 ]; then
    FILES="$@"
else
    # The scripts that exercise-one-database.sh runs, and the helpers they call.
    # The demo scripts (demo*.sh, checkin-all.sh, setup-*.sh) feed a demo
    # server and gate nothing, so they're not checked.
    FILES=$(ls test-*.sh import-roster.sh run-scout-heats.sh photo-setup.sh)
fi

IDNAME='(racer|racerid|roundid|classid|rankid|partitionid|awardid)'
NUM='[1-9][0-9]*'
PATTERNS=(
    "\\b${IDNAME}=${NUM}\\b"
    "\\bconstituent_${NUM}\\b"
    "\\b(classid|rankid|partitionid|awardid|racerid)_${NUM}=[0-9]"
    "\\bkey=(speed-[0-9]+[a-z]?-${NUM}|award-${NUM})"
    "\\brun_heat( -place)? ${NUM} "
    "\\.${IDNAME} *(==|!=|<=|>=|<|>) *${NUM}\\b"
    "\"${IDNAME}\": *${NUM}\\b"
    "\\bvotes=\\[[0-9]"
    "\\bclassids=[0-9]"
    "\\bwho=[cr][0-9]"
    "\\bclass_and_rank=${NUM},"
    "\\bclass_and_rank=[0-9]+,${NUM}"
    "\\b${IDNAME}[a-z_]* *== *\\[[0-9]"
    "\\(\\.${IDNAME}\\) *\\| *sort *== *\\[[0-9]"
)

FOUND=$(mktemp)
USED=$(mktemp)
trap 'rm -f $FOUND $USED' EXIT

for f in $FILES; do
    # Comment lines don't run, and a line may be a continuation, so look at each
    # line on its own.
    grep -nE -e "$(IFS='|'; echo "${PATTERNS[*]}")" "$f" | grep -vE '^[0-9]+:[[:space:]]*#' | \
        while IFS= read -r hit; do
            line="${hit#*:}"
            allowed=
            if [ -f "$ALLOWLIST" ]; then
                while IFS=$'\t' read -r afile apiece reason; do
                    [[ -z "$afile" || "$afile" == \#* ]] && continue
                    if [ "$afile" = "$f" ] && [[ "$line" == *"$apiece"* ]]; then
                        allowed=1
                        echo "$afile	$apiece" >> $USED
                    fi
                done < "$ALLOWLIST"
            fi
            [ -n "$allowed" ] || echo "$f:$hit" >> $FOUND
        done
done

STATUS=0
if [ -s $FOUND ]; then
    echo "Literal database IDs in the test scripts.  Look the ID up by a stable key (see"
    echo "testing/common.sh: racerid_of, roundid_of, awardid_of, classid_of, ...) or take it"
    echo "from the response that created the thing:"
    echo
    cut -c1-160 $FOUND
    STATUS=1
fi

if [ -f "$ALLOWLIST" ] && [ $# -eq 0 ]; then
    while IFS=$'\t' read -r afile apiece reason; do
        [[ -z "$afile" || "$afile" == \#* ]] && continue
        if ! grep -qxF "$afile	$apiece" $USED; then
            echo "Allowlist entry matches nothing: $afile  $apiece"
            STATUS=1
        fi
    done < "$ALLOWLIST"
fi

exit $STATUS
