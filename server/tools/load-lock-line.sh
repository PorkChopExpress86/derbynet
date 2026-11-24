# Sourced by tools/pytest-under-load.sh and tools/hold-load-lock.sh: the
# line their runs wait in, so the load lock goes to its waiters in turn
# (#417).  flock(2) promises no order: the kernel wakes every waiter at the
# release and the first to run takes the lock.  On 2026-11-18 a queued step
# lost the lock to a run that started waiting after it, and one rerun waited
# 7206 s.
#
# The line is the folder LOCK.line, LOCK taken as a real path so that
# `dir/./load.lock` waits in the same line.  A waiter's entry is the file
# TICKET.PID in it, TICKET one more than the highest live entry's, holding
# `kind KIND` (hand-off or load-run) and then the lines it will write as the
# holder.  The waiter holds an flock on its entry (fd 8) until it leaves the
# line, and gives no child a copy, so a free flock on an entry means its
# owner has died: a pid used again cannot keep a dead entry in the line.
# Entries are added, and dead ones removed, only under the flock of
# LOCK.line/mutex (fd 7), held for that alone, so no one removes an entry
# between its creation and its flock.  A waiter removes its own entry with
# no lock: the pid in its name keeps any other run from making that name
# while it lives.
#
# The line serves every hand-off before any load run, and each kind in the
# order it came, so a hand-off of a few minutes waits for the holder and the
# hand-offs ahead of it only (#419: #406's 3-minute hand-off waited 7585 s
# behind a 25-count run that had queued before it).  An entry with no
# `kind` line comes from a copy of the scripts from before #419, which
# serves the line in ticket order alone; while one is in the line, every
# waiter does the same, since two orders in one line can wait on each other
# for ever.
#
# The run first in that order tries the load lock (fd 9, in the script)
# once a second, each try under the mutex right after it reads the line, so
# a hand-off that joined first is never passed: a waiter blocked in
# `flock -x` took the lock at the release whatever had joined since.  A
# try costs up to 1 s of free lock after a release.  The others read the
# line every second too, and each run prints its place when it changes.
# The mutex is never held across the sleep.  The flock on the load lock
# stays the mutual exclusion: a run from a branch cut before the line takes
# it with no entry, so it may go ahead of the line, but never runs beside a
# holder.
#
# A run that does not hold the lock never runs its command, whatever fails
# on the way (#417 round 4).  An entry's name is TICKET.PID with a ticket of
# ten digits; any other name in the line folder is not an entry, and the
# scan leaves it alone.  A failed command ends the run through line_fail,
# which each script sets as its ERR trap from before line_join to the end of
# the wait.  Bash also drops a whole command on an arithmetic error and goes
# on with the next, with no failed status for that trap to see, so
# line_join says what happened in LINE_STATE, and a script that finds it
# neither `took` nor `queued` calls line_fail itself.

ENTRY=
LINE=
LINE_STATE=
ENTRY_NAME='^[0-9]{10}\.[0-9]+$'
LINE_ENTRIES=()
LINE_LAST=0
LINE_SHOWN=

# Ends the run with 2 and a message: its command has not run, and a run that
# holds no lock must not.  $1 says what failed.
line_fail() {
  echo "${0##*/}: it could not join the line or take the load lock ($1);" \
       "it runs no command." >&2
  exit 2
}

# Under the mutex: removes each entry whose owner has died, lists the others
# in the order the line serves them in LINE_ENTRIES, and sets LINE_LAST to
# the highest ticket among them, 0 for none.
line_scan() {
  local entry first last marked=1 all=() handoffs=() loads=()
  LINE_ENTRIES=()
  LINE_LAST=0
  for entry in "$LINE"/*; do
    # The mutex, an editor's backup and the like are no entries, and an
    # entry's owner made a file.
    [[ ${entry##*/} =~ $ENTRY_NAME ]] || continue
    [ -f "$entry" ] || continue
    # `6<` opens the entry without creating it, as `flock FILE` would.  An
    # entry its owner removed since the glob fails to open or to read,
    # which is no error here.
    if flock -n -x 6 2>/dev/null 6<"$entry"; then
      rm -f -- "$entry"
      continue
    fi
    if ! IFS= read -r first 2>/dev/null <"$entry"; then
      continue
    fi
    all+=("$entry")
    case $first in
      "kind hand-off") handoffs+=("$entry") ;;
      "kind load-run") loads+=("$entry") ;;
      *) marked= ;;
    esac
  done
  # The glob sorts the ten-digit tickets, so the last entry's is the highest.
  if [ ${#all[@]} != 0 ]; then
    last=${all[-1]##*/}
    LINE_LAST=$((10#${last%%.*}))
  fi
  if [ -n "$marked" ]; then
    LINE_ENTRIES=("${handoffs[@]}" "${loads[@]}")
  else
    LINE_ENTRIES=("${all[@]}")
  fi
}

# Takes the load lock on fd 9 at once when no one waits in the line and no
# one holds it, leaving ENTRY empty and LINE_STATE `took`; otherwise adds an
# entry of KIND (hand-off or load-run) holding TEXT, sets ENTRY, and ends
# with LINE_STATE `queued`.  Called as `line_join KIND TEXT`.
line_join() {
  LINE_STATE=
  # The script has opened LOCK on fd 9, so it exists and realpath needs no
  # -m.
  LINE="$(realpath -- "$LOCK").line"
  if ! { mkdir -p "$LINE" && exec 7>>"$LINE/mutex"; } 2>/dev/null; then
    echo "${0##*/}: it cannot make the line $LINE beside the load lock;" \
         "name a file in a writable folder in DERBYNET_LOAD_LOCK." >&2
    exit 2
  fi
  flock -x 7
  line_scan
  if [ ${#LINE_ENTRIES[@]} = 0 ] && flock -n -x 9; then
    exec 7>&-
    LINE_STATE=took
    return
  fi
  # Named before it exists, so the cleanup removes it from its creation on.
  ENTRY="$LINE/$(printf '%010d' $((LINE_LAST + 1))).$$"
  exec 8>"$ENTRY"
  flock -x 8
  printf 'kind %s\n%s\n' "$1" "$2" >&8
  exec 7>&-
  LINE_STATE=queued
}

# Prints this run's place and the runs ahead of it whenever they change,
# and returns once it holds the load lock: it tries the lock only when none
# is ahead, under the mutex it read the line under.  A try that finds the
# lock held fails with 1; any other failure ends the run.
line_wait_turn() {
  local entry text ahead shown try_status
  while :; do
    exec 7>>"$LINE/mutex"
    flock -x 7
    line_scan
    ahead=()
    for entry in "${LINE_ENTRIES[@]}"; do
      [ "$entry" = "$ENTRY" ] && break
      text=$(cat -- "$entry" 2>/dev/null) || continue
      text=${text#kind *$'\n'}
      ahead+=("  ${text//$'\n'/; }")
    done
    try_status=1
    if [ ${#ahead[@]} = 0 ]; then
      try_status=0
      flock -n -x 9 || try_status=$?
    fi
    exec 7>&-
    if [ ${#ahead[@]} = 0 ]; then
      shown="Place 1 in line: it takes the load lock next."
    else
      shown="Place $(( ${#ahead[@]} + 1 )) in line, behind:"
      shown+=$(printf '\n%s' "${ahead[@]}")
    fi
    if [ "$shown" != "$LINE_SHOWN" ]; then
      printf '%s\n' "$shown"
      LINE_SHOWN=$shown
    fi
    if [ "$try_status" = 0 ]; then
      return
    fi
    if [ "$try_status" != 1 ]; then
      line_fail "flock on the load lock exited $try_status"
    fi
    sleep 1 8>&- 9>&- &
    wait "$!"
  done
}

# Takes this run's entry out of the line.
line_leave() {
  if [ -n "$ENTRY" ]; then
    rm -f -- "$ENTRY"
    exec 8>&-
    ENTRY=
  fi
}
