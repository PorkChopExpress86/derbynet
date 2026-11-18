# Sourced by tools/pytest-under-load.sh and tools/hold-load-lock.sh: the
# line their runs wait in, so the load lock goes to its waiters in the
# order they came (#417).  flock(2) promises no order: the kernel wakes
# every waiter at the release and the first to run takes the lock.  On
# 2026-11-18 a queued step lost the lock to a run that started waiting
# after it, and one rerun waited 7206 s.
#
# The line is the folder LOCK.line, LOCK taken as a real path so that
# `dir/./load.lock` waits in the same line.  A waiter's entry is the file
# TICKET.PID in it, TICKET one more than the last live entry's, holding the
# lines it will write as the holder.  The waiter holds an flock on its
# entry (fd 8) until it leaves the line, and gives no child a copy, so a
# free flock on an entry means its owner has died: a pid used again cannot
# keep a dead entry in the line.  Entries are added, and dead ones removed,
# only under the flock of LOCK.line/mutex (fd 7), held for that alone, so
# no one removes an entry between its creation and its flock.  A waiter
# removes its own entry with no lock: the pid in its name keeps any other
# run from making that name while it lives.
#
# Only the first live entry waits on the load lock (fd 9, in the script).
# The others read the line every second and print their place when it
# changes.  The flock on the load lock stays the mutual exclusion: a run
# from a branch cut before the line takes it with no entry, so it may go
# ahead of the line, but never runs beside a holder.

ENTRY=
LINE=
LINE_ENTRIES=()
LINE_SHOWN=

# Under the mutex: removes each entry whose owner has died, and lists the
# others, in the order they came, in LINE_ENTRIES.
line_scan() {
  local entry
  LINE_ENTRIES=()
  for entry in "$LINE"/[0-9]*.[0-9]*; do
    # `6<` opens the entry without creating it, as `flock FILE` would.  An
    # entry its owner removed since the glob fails to open, which is no
    # error here.
    if flock -n -x 6 2>/dev/null 6<"$entry"; then
      rm -f -- "$entry"
    elif [ -e "$entry" ]; then
      LINE_ENTRIES+=("$entry")
    fi
  done
}

# Takes the load lock on fd 9 at once when no one waits in the line and no
# one holds it, leaving ENTRY empty; otherwise adds an entry holding TEXT
# and sets ENTRY.
line_join() {
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
  local count=${#LINE_ENTRIES[@]} last=0
  if [ "$count" = 0 ] && flock -n -x 9; then
    exec 7>&-
    return
  fi
  if [ "$count" != 0 ]; then
    last=${LINE_ENTRIES[count - 1]##*/}
    last=$((10#${last%%.*}))
  fi
  # Named before it exists, so the cleanup removes it from its creation on.
  ENTRY="$LINE/$(printf '%010d' $((last + 1))).$$"
  exec 8>"$ENTRY"
  flock -x 8
  printf '%s\n' "$1" >&8
  exec 7>&-
}

# Prints this run's place and the runs ahead of it whenever they change,
# and returns once none is ahead: its turn to wait on the load lock.
line_wait_turn() {
  local entry text ahead shown
  while :; do
    exec 7>>"$LINE/mutex"
    flock -x 7
    line_scan
    ahead=()
    for entry in "${LINE_ENTRIES[@]}"; do
      [ "$entry" = "$ENTRY" ] && break
      text=$(cat -- "$entry" 2>/dev/null) || continue
      ahead+=("  ${text//$'\n'/; }")
    done
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
    if [ ${#ahead[@]} = 0 ]; then
      return
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
