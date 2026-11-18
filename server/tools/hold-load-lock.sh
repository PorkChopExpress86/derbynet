#! /bin/bash
#
# Runs a command while it holds tools/pytest-under-load.sh's lock, so a
# hand-off's runs without load queue behind a load run and keep the next
# one out until they end.
#
# Usage: server/tools/hold-load-lock.sh COMMAND [ARGUMENT ...]
#
#   server/tools/hold-load-lock.sh bash ~/.cache/derbynet-drafts/N/handoff.sh
#
# A hand-off runs alone (plan/AGENT-BRIEF.md), and a plain pytest run only
# names a load run's holder (D114).  #350's hand-off polled for a free lock
# and waited about 2 h: #348's queued load runs took the lock again within
# seconds of each other, so its gate waited for a 5-minute free window
# (#348, #350 retro).  This script waits in the same line as the load runs
# instead, and takes the lock in the order it came, ahead of any run that
# starts waiting after it (tools/load-lock-line.sh, #417).
#
# It takes the lock as pytest-under-load.sh does (DERBYNET_LOAD_LOCK, or
# ~/.cache/derbynet/load-run.lock), prints its place in the line as that
# script does, writes the same holder lines, with
# `args hold-load-lock.sh: COMMAND ...`, and runs COMMAND in a process group
# of its own with DERBYNET_LOAD_LOCK_HELD set, so the pytest runs under it
# name no holder (conftest.py).  COMMAND gets no copy of the lock, so a
# SIGKILL of this script frees it at once; a SIGTERM, SIGINT or SIGHUP stops
# COMMAND's group too.  It exits with COMMAND's status.
#
# COMMAND must not start tools/pytest-under-load.sh on the same lock, which
# would wait on this script for ever: that script refuses to start under a
# holder of its own lock.  Run the load repeats after this script ends.

set -e -E -o pipefail

if [ $# -eq 0 ]; then
  echo "usage: hold-load-lock.sh COMMAND [ARGUMENT ...]" >&2
  exit 2
fi
if ! command -v flock >/dev/null; then
  echo "hold-load-lock.sh: it needs flock(1): install util-linux, or on macOS" \
       "\`brew install flock\`." >&2
  exit 2
fi

LOCK="${DERBYNET_LOAD_LOCK:-}"
if [ -z "$LOCK" ]; then
  if [ -z "${HOME:-}" ]; then
    echo "hold-load-lock.sh: set HOME or DERBYNET_LOAD_LOCK." >&2
    exit 2
  fi
  LOCK="${XDG_CACHE_HOME:-$HOME/.cache}/derbynet/load-run.lock"
fi
# Compared as real paths: `dir/./load.lock` or a link is the same lock.
if [ -n "${DERBYNET_LOAD_LOCK_HELD:-}" ] &&
   [ "$(realpath -m -- "$DERBYNET_LOAD_LOCK_HELD")" = "$(realpath -m -- "$LOCK")" ]; then
  echo "hold-load-lock.sh: it runs under a holder of $LOCK already, and would" \
       "wait on it for ever." >&2
  exit 2
fi
if ! { mkdir -p "$(dirname "$LOCK")" && exec 9>>"$LOCK"; } 2>/dev/null; then
  echo "hold-load-lock.sh: it cannot open the load lock $LOCK; name a" \
       "writable file in DERBYNET_LOAD_LOCK." >&2
  exit 2
fi
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
. "$(dirname "${BASH_SOURCE[0]}")/load-lock-line.sh"

WAITING=
CHILD=
stop_child() {
  # `jobs -p` names the waiting flock only until `wait` reaps it, as in
  # pytest-under-load.sh, which says why (#391 review).
  if [ -n "$WAITING" ]; then
    waiter=$(jobs -p)
    if [ -n "$waiter" ]; then kill "$waiter" 2>/dev/null || true; fi
  fi
  if [ -n "$CHILD" ]; then kill -TERM -- "-$CHILD" 2>/dev/null || true; fi
  line_leave
}
trap stop_child EXIT
# A signal ignores further signals, as in pytest-under-load.sh, which says
# why.  Its cleanup needs no ignore of its own: a command that fails under
# `set -e` leaves it nothing to stop, since a failed `wait` has reaped its
# flock, which `jobs -p` then no longer names, and no command between
# `CHILD=$!` and `CHILD=` can fail.  The traps come before the wait for the
# lock, as there (#391).
trap 'trap "" TERM INT HUP; exit 143' TERM INT HUP

line_join "$(printf 'pid %s, since %s\ncwd %s\nhead %s\nargs hold-load-lock.sh: %s' \
  "$$" "$(date '+%Y-%m-%d %H:%M:%S')" "$PWD" "$HEAD_SHA" "$*")"
if [ -n "$ENTRY" ]; then
  echo "Waiting for the load lock $LOCK, held by:"
  { [ -s "$LOCK" ] && sed 's/^/  /' "$LOCK"; } || echo "  (no details yet)"
  since=$(date +%s)
  # Waited on in the background, as in pytest-under-load.sh, which says why.
  WAITING=1
  line_wait_turn
  flock -x 9 8>&- &
  wait "$!"
  WAITING=
  echo "Got the load lock after $(( $(date +%s) - since )) s"
fi
printf 'pid %s, since %s\ncwd %s\nhead %s\nargs hold-load-lock.sh: %s\n' \
  "$$" "$(date '+%Y-%m-%d %H:%M:%S')" "$PWD" "$HEAD_SHA" "$*" >"$LOCK"
# Out of the line, now that it holds the lock.
line_leave

# setsid gives COMMAND a process group of its own (its pid), which the trap
# stops whole; `9>&-` keeps the lock with this script alone.  `<&0` keeps
# this script's stdin, which bash gives a background command as /dev/null.
DERBYNET_LOAD_LOCK_HELD="$LOCK" setsid "$@" 9>&- <&0 &
CHILD=$!
status=0
wait "$CHILD" || status=$?
CHILD=
exit "$status"
