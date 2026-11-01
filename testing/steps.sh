# The steps of exercise-one-database.sh, which sources this file after
# common.sh.
#
#   step [--resets] <script.sh | function> [args ...]
#   skip_step <label> <reason>
#   steps_done
#
# A script in testing/ runs as `<script> $BASE_URL args ...`, as the suite has
# always called it; a shell function runs with its arguments alone.
#
# Without PARITY_DIR, a step just runs, and a failure ends the run (set -e).
#
# With PARITY_DIR set, the run is a leg of the parity gate (#37,
# .github/workflows/parity.yml), and server/tools/parity_gate.py decides what
# fails:
#
# - The exchanges a step appends to output.curl are copied to
#   $PARITY_DIR/<nn>-<label>.curl, so that the gate compares the two servers
#   step by step, and a step that goes wrong on one server doesn't hide the
#   steps after it.
# - Each step adds a line to $PARITY_DIR/steps.tsv: its number, its label (the
#   script and its arguments, with " (2)" and so on when it runs again), its
#   outcome (pass, fail or skip), its attempts, its transcript, and the reason
#   for a skip.
# - A failed step marked --resets is run once more.  It resets the database
#   itself, so the second attempt starts from the same state as the first; the
#   transcript is the second attempt's.  Another step can't be retried on its
#   own, because it carries on from the state the steps before it left.
# - The run goes on after a failed step.

STEP_NUMBER=0
STEP_LABELS=

# The number of bytes in output.curl so far.
_step_offset() {
    if [ -f "$OUTPUT_CURL" ] ; then
        wc -c < "$OUTPUT_CURL" | tr -d ' '
    else
        echo 0
    fi
}

# Sets STEP_LABEL to the label of a step: its words, and " (n)" for the nth run
# of the same words.  Not run in $(...), which would lose STEP_LABELS.
_step_label() {
    local words="$*"
    local count
    count=$(printf '%s' "$STEP_LABELS" | grep -c -x -F -- "$words" || true)
    STEP_LABELS="$STEP_LABELS$words
"
    if [ "$count" -gt 0 ] ; then
        STEP_LABEL="$words ($((count + 1)))"
    else
        STEP_LABEL="$words"
    fi
}

_step_file() {
    printf '%02d-%s.curl' "$STEP_NUMBER" "$(echo "$1" | tr -c 'A-Za-z0-9.\n-' '_')"
}

step() {
    local resets=
    if [ "$1" = "--resets" ] ; then
        resets=1
        shift
    fi
    local command=("$@")
    case "$1" in
        *.sh) command=("`dirname $0`/$1" "$BASE_URL" "${@:2}") ;;
    esac

    if [ -z "$PARITY_DIR" ] ; then
        "${command[@]}"
        return
    fi

    STEP_NUMBER=$((STEP_NUMBER + 1))
    _step_label "$@"
    local label="$STEP_LABEL" file
    file=$(_step_file "$label")

    local attempts=0 status start
    while : ; do
        attempts=$((attempts + 1))
        start=$(_step_offset)
        # Not in an `if`, which would turn off set -e inside a function step.
        set +e
        ( set -e ; "${command[@]}" )
        status=$?
        set -e
        if [ $status -eq 0 ] || [ -z "$resets" ] || [ $attempts -ge 2 ] ; then
            break
        fi
        tput setaf 3  # yellow text
        echo "STEP FAILED, RETRYING ONCE: $label"
        tput setaf 0
    done

    tail -c +$((start + 1)) "$OUTPUT_CURL" > "$PARITY_DIR/$file" 2>/dev/null || true
    local outcome=pass
    if [ $status -ne 0 ] ; then
        outcome=fail
        tput setaf 1  # red text
        echo "STEP FAILED: $label"
        tput setaf 0
    fi
    printf '%s\t%s\t%s\t%s\t%s\t\n' \
           "$STEP_NUMBER" "$label" "$outcome" "$attempts" "$file" >> "$PARITY_DIR/steps.tsv"
}

skip_step() {
    tput setaf 3  # yellow text
    echo "Skipping $1: $2"
    tput setaf 0
    if [ -n "$PARITY_DIR" ] ; then
        STEP_NUMBER=$((STEP_NUMBER + 1))
        _step_label "$1"
        local label="$STEP_LABEL"
        printf '%s\t%s\tskip\t0\t\t%s\n' "$STEP_NUMBER" "$label" "$2" >> "$PARITY_DIR/steps.tsv"
    fi
}

# Print each step's outcome, in a parity gate leg.
steps_done() {
    if [ -n "$PARITY_DIR" ] ; then
        echo
        echo "Steps (see $PARITY_DIR/steps.tsv):"
        cut -f 1-4,6 "$PARITY_DIR/steps.tsv"
    fi
}
