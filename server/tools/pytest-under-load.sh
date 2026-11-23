#! /bin/bash
#
# Runs pytest while busy loops load the CPU, the way a flaky browser test is
# reproduced and a fix proved, and a changed browser test repeated (25 for a
# flake or a timing test, 5 per font for a deterministic one: owner,
# 2026-11-11, server/README.md "Browser tests"; a kiosk file's repeats
# run in the default font only, D133).  It keeps every result:
# pytest's whole output goes to a log file and its results to a junit file,
# with no pipe and no time limit,
# so a long run is never cut short or filtered.  It stops only the loops it
# started.
#
# Usage: server/tools/pytest-under-load.sh [-h | --help] [--postgres] [--loops N] [--flake-ticket] [pytest argument ...]
#
#   server/tools/pytest-under-load.sh --postgres -m browser -n 4 --count 25 -k phone ui/tests/browser/test_photos.py
#
# -h or --help among its own options, which are read first, prints the Usage
# line and exits 0 before any refusal and before the lock; given after the
# first pytest argument it reaches pytest.  --postgres runs through
# tools/pytest-postgres.sh, and starts the loops once
# its Postgres is ready: started under load, it once missed a 300 s wait
# (#54 retro).  --loops sets the number
# of busy loops (default 8).  Results go to var/load-runs/<time>/ under
# server/, or DERBYNET_LOAD_RUN_DIR, which must be new or empty: pytest.log
# and junit.xml.  At the end it prints pytest's summary lines, with -rfE's
# list of failures and errors, and the log's path.  It exits with pytest's
# status, unless a rerun after net::ERR_NETWORK_CHANGED passes the run
# (below).  Run it with the Bash tool's run_in_background rather than under
# `timeout`, which loses the run.
#
# When every failure and error is net::ERR_NETWORK_CHANGED, as a raised
# Playwright error or the browser harness's teardown assert naming it
# (tools/network_changed.py), it reruns the failed files alone, with no
# loops and `-n 0` (none beside `-p no:xdist`), before it frees the lock,
# keeping the run's database, font and `-m`, `-k` and `-p` options, into
# rerun.log and rerun-junit.xml beside the first run's (D122).  With veth lines in the
# kernel log over the first run and a clean rerun, it exits 0: the run
# counts as passed for D101's repeats only.  --flake-ticket keeps pytest's
# status whatever the rerun does: a flake ticket's race keeps its 25 runs.
# #308, #317, #341 and #348 each started that rerun by hand, sometimes after
# the lock had gone to the next run.
#
# One load run at a time per machine: it takes an exclusive flock on
# DERBYNET_LOAD_LOCK (default ~/.cache/derbynet/load-run.lock) before it
# starts anything, and a second run waits for it, printing the holder's pid,
# folder, commit and arguments, which the holder writes into the file.  #316's
# hand-off ran beside #317's 25-count run at a load average of 35 and failed,
# then waited 74 min for a slot the lead handed out by message (#316 retro).
# Runs wait in a line and take the lock in the order they came, each
# printing its place and the runs ahead of it, again whenever that changes
# (tools/load-lock-line.sh, #417).  Whatever fails on the way to the lock,
# the run ends with 2 and a message and starts nothing; a file in the line
# folder that it did not make is ignored.
# Only this script holds the lock (its loops and pytest get no copy of it),
# so a SIGKILL frees it at once.  A SIGKILL while it waits leaves its flock
# waiting, which takes the lock once it frees and exits at once, and its
# entry in the line, which the next waiter removes; a SIGTERM, SIGINT or
# SIGHUP stops that flock and removes the entry (#391).  Its pytest gets
# DERBYNET_LOAD_LOCK_HELD, and any other pytest run names the holder in its
# header and summary (conftest.py).

set -e -E -o pipefail

# Its own options come first, and -h or --help among them prints the usage
# and takes no lock: hold-load-lock.sh --help once queued on the lock as a
# COMMAND, and this script would run `pytest --help` under load (#434 retro).
# They are read before the refusals below, so --help works through a pipe.
SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER=(uv run pytest)
ALL_ARGS="$*"
LOOPS=8
FLAKE_TICKET=
usage() {
  sed -n 's/^# \(Usage: .*\)/\1/p' "$0"
  echo "The comment at the top of $0 says more."
}
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --postgres) RUNNER=("$SERVER_DIR/tools/pytest-postgres.sh"); shift ;;
    --loops) LOOPS="$2"; shift 2 ;;
    --flake-ticket) FLAKE_TICKET=--flake-ticket; shift ;;
    *) break ;;
  esac
done

# It refuses to start through a pipe or under `timeout`: #47 and #48 each
# lost a 25-repeat run to `timeout 3000 ... | grep`, killed with nothing
# kept (#48 retro).
refuse() {
  echo "pytest-under-load.sh: $1. Run it with the Bash tool's run_in_background," \
       "with no pipe and no timeout, and read the summary it prints." >&2
  exit 2
}
[ -p /dev/stdout ] && refuse "its output is a pipe"
pid=$PPID
while [ "${pid:-0}" -gt 1 ]; do
  [ "$(ps -o comm= -p "$pid" || true)" = timeout ] && refuse "it runs under timeout"
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ') || pid=1
done
# pytest-repeat's --count renames each case `[P]` to `[P-1-N]` .. `[P-N-N]`,
# so a node id that names `[P]` selects nothing: #463's run waited for the
# lock, then collected 0 items and exited 5 (#463 retro).  A count of 1
# renames nothing.
count=
next=
for arg in "$@"; do
  if [ -n "$next" ]; then count=$arg; next=; continue; fi
  case "$arg" in
    --count) next=1 ;;
    --count=*) count=${arg#--count=} ;;
  esac
done
if [[ "$count" =~ ^[1-9][0-9]*$ ]] && [ "$count" -gt 1 ]; then
  for arg in "$@"; do
    if [[ "$arg" == *::*\[* && ! "$arg" =~ -[0-9]+-[0-9]+\]$ ]]; then
      echo "pytest-under-load.sh: --count renames the [case] of $arg to" \
           "[case-1-N] .. [case-N-N], so it selects no test. Name the test" \
           "without its [case], and choose the case with -k." >&2
      exit 2
    fi
  done
fi
# The lock needs util-linux's flock(1), which macOS lacks (#354 review).
if ! command -v flock >/dev/null; then
  echo "pytest-under-load.sh: it needs flock(1), for the lock that keeps one" \
       "load run at a time: install util-linux, or on macOS \`brew install flock\`." >&2
  exit 2
fi

OUT="${DERBYNET_LOAD_RUN_DIR:-$SERVER_DIR/var/load-runs/$(date +%Y%m%d-%H%M%S)-$$}"
# A folder that already holds files is refused: #220 reused one, and a wait
# for its junit.xml fired at once on an earlier session's results (#220
# retro).  Wait on this script's own last line, "pytest exited N".  It is
# checked again once the lock is taken, since two runs waiting on one new
# folder both passed the first check (#354 review).
refuse_used_folder() {
  if [ -d "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
    echo "pytest-under-load.sh: $OUT already holds files. Name a folder that" \
         "does not exist yet, or leave DERBYNET_LOAD_RUN_DIR unset." >&2
    exit 2
  fi
}
refuse_used_folder

LOCK="${DERBYNET_LOAD_LOCK:-}"
if [ -z "$LOCK" ]; then
  if [ -z "${HOME:-}" ]; then
    echo "pytest-under-load.sh: set HOME or DERBYNET_LOAD_LOCK, the lock" \
         "that keeps one load run at a time on this machine." >&2
    exit 2
  fi
  LOCK="${XDG_CACHE_HOME:-$HOME/.cache}/derbynet/load-run.lock"
fi
# Under a holder of the same lock, such as tools/hold-load-lock.sh's
# command or a load run's own pytest, flock would wait on that holder for
# ever.
# Compared as real paths: `dir/./load.lock` or a link is the same lock.
if [ -n "${DERBYNET_LOAD_LOCK_HELD:-}" ] &&
   [ "$(realpath -m -- "$DERBYNET_LOAD_LOCK_HELD")" = "$(realpath -m -- "$LOCK")" ]; then
  echo "pytest-under-load.sh: it runs under a holder of $LOCK, such as" \
       "tools/hold-load-lock.sh, and would wait on it for ever. Run it after" \
       "that holder ends." >&2
  exit 2
fi
# Appending, so a run that waits leaves the holder's lines in place.
if ! { mkdir -p "$(dirname "$LOCK")" && exec 9>>"$LOCK"; } 2>/dev/null; then
  echo "pytest-under-load.sh: it cannot open the load lock $LOCK; name a" \
       "writable file in DERBYNET_LOAD_LOCK." >&2
  exit 2
fi
# The commit is read before the lock, so a slow git never leaves a waiter
# reading half the holder's lines (#354 review).
HEAD_SHA="$(git -C "$SERVER_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
LINE_FILE="$SERVER_DIR/tools/load-lock-line.sh"
. "$LINE_FILE" || {
  echo "pytest-under-load.sh: it cannot read $LINE_FILE, which keeps the" \
       "line the load lock is served in; it runs no command." >&2
  exit 2
}

WAITING=
PIDS=()
RUN_PID=
stop_loops() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  PIDS=()
}
stop_all() {
  trap '' TERM INT HUP
  # `jobs -p` names the waiting flock from its fork until `wait` reaps it,
  # and nothing after: a kept pid missed a signal before it was kept, and
  # named a flock already gone after (#391 review).
  if [ -n "$WAITING" ]; then
    waiter=$(jobs -p)
    if [ -n "$waiter" ]; then kill "$waiter" 2>/dev/null || true; fi
  fi
  stop_loops
  if [ -n "$RUN_PID" ]; then kill "$RUN_PID" 2>/dev/null || true; fi
  line_leave
}
trap stop_all EXIT
# A signal ignores any further TERM, INT or HUP, and so does the cleanup,
# for an EXIT trap no signal started, such as an error under `set -e`: a
# signal's `exit 143` inside the EXIT trap would end it before the loops
# still listed were killed, and they ran on after the script exited (#372).
# Both traps come before the wait for the lock: set after it, a TERM during
# the wait killed the script and left its flock waiting (#391).
trap 'trap "" TERM INT HUP; exit 143' TERM INT HUP

trap 'line_fail "$BASH_COMMAND"' ERR
line_join "$(printf 'pid %s, since %s\ncwd %s\nhead %s\nargs %s' \
  "$$" "$(date '+%Y-%m-%d %H:%M:%S')" "$PWD" "$HEAD_SHA" "$ALL_ARGS")"
if [ "$LINE_STATE" = queued ]; then
  echo "Waiting for the load lock $LOCK, held by:"
  # Empty from the holder's truncation until its one write lands.
  { [ -s "$LOCK" ] && sed 's/^/  /' "$LOCK"; } || echo "  (no details yet)"
  since=$(date +%s)
  # In the background, so a signal's trap runs during the wait, and the
  # cleanup stops the flock, the only job then: bash runs a trap only once a
  # foreground command ends.  line_wait_turn sleeps the same way.  The lock
  # is on the file fd 9 opened, so it stays with this script once flock
  # exits; a flock that ends without it fails the script here.  `8>&-`
  # keeps the entry's flock with this script alone.
  WAITING=1
  line_wait_turn
  flock -x 9 8>&- &
  wait "$!"
  WAITING=
  echo "Got the load lock after $(( $(date +%s) - since )) s"
elif [ "$LINE_STATE" != took ]; then
  line_fail "the join gave no place and no lock"
fi
trap - ERR
refuse_used_folder
# One write of the whole text.
printf 'pid %s, since %s\ncwd %s\nhead %s\nargs %s\n' \
  "$$" "$(date '+%Y-%m-%d %H:%M:%S')" "$PWD" "$HEAD_SHA" "$ALL_ARGS" >"$LOCK"
# Out of the line, now that it holds the lock.
line_leave

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

cd "$SERVER_DIR"
echo "Under load ($LOOPS loops): ${RUNNER[*]} $*"
echo "Log: $OUT/pytest.log"
echo "Load lock: $LOCK"
READY="$OUT/postgres-ready"
rm -f "$READY"
# `9>&-`: no child keeps the lock, so it ends with this script.
DERBYNET_PG_READY_FILE="$READY" DERBYNET_LOAD_LOCK_HELD="$LOCK" \
  "${RUNNER[@]}" -rfE --junitxml="$OUT/junit.xml" "$@" >"$OUT/pytest.log" 2>&1 9>&- &
RUN_PID=$!
STARTED=$(date +%s)
if [ "${RUNNER[0]}" != uv ]; then
  while [ ! -e "$READY" ] && kill -0 "$RUN_PID" 2>/dev/null; do sleep 1; done
fi
for _ in $(seq 1 "$LOOPS"); do
  bash -c 'while :; do :; done' 9>&- &
  PIDS+=("$!")
done
status=0
wait "$RUN_PID" || status=$?
ENDED=$(date +%s)
RUN_PID=
stop_loops

# The short test summary (-rfE: errors too, such as a teardown's, which -rf
# left unnamed in the #220 retro's run) and the final line, which names an
# error such as a wrong path as well as the counts.
sed -n '/short test summary info/,$p' "$OUT/pytest.log"
grep -q 'short test summary info' "$OUT/pytest.log" || tail -n 5 "$OUT/pytest.log"
# Chromium's net::ERR_NETWORK_CHANGED marks the machine's network changing,
# as another project's container joins or leaves a Docker bridge. The rule
# for it (D122) needs the kernel log's veth lines over the run, which #348
# read by hand three times (#348 retro), and a rerun of the failed files
# alone, which tools/network_changed.py names when every failure is one.
final=$status
if [ "$status" != 0 ] && grep -q 'net::ERR_NETWORK_CHANGED' "$OUT/pytest.log"; then
  veth="no journalctl here"
  veth_count=
  if command -v journalctl >/dev/null; then
    # A failed read is named, not counted: 0 veth lines would read as a
    # real failure under the rerun rule.  So is a journalctl that reads no
    # kernel log at all, as one without permission exits 0 with no lines.
    if [ -z "$(journalctl -k -n 1 -q 2>/dev/null)" ]; then
      veth="no count (journalctl reads no kernel log here)"
    elif journal=$(journalctl -k --since "@$STARTED" --until "@$ENDED" 2>&1); then
      veth_count=$(grep -c veth <<<"$journal" || true)
      veth="$veth_count veth lines"
    else
      veth="no count (journalctl failed: $(head -n 1 <<<"$journal"))"
    fi
  fi
  echo "net::ERR_NETWORK_CHANGED in the log; the kernel log has $veth from" \
       "@$STARTED to @$ENDED (journalctl -k --since @$STARTED --until @$ENDED | grep veth)."
  # The arguments go to a file of their own, so a reason is never run as
  # one; any exit but 0 and 1 is the tool's own error (#373 review).
  tool=0
  reason=$(python3 -I "$SERVER_DIR/tools/network_changed.py" rerun-args \
             "$OUT/junit.xml" "$OUT/pytest.log" "$OUT/rerun-args" -- "$@") || tool=$?
  if [ "$tool" = 0 ]; then
    mapfile -t RERUN <"$OUT/rerun-args"
    echo "Rerunning the failed files alone, with no load, before the lock is freed:" \
         "${DERBYNET_BROWSER_FONT:+DERBYNET_BROWSER_FONT=\"$DERBYNET_BROWSER_FONT\" }${RUNNER[*]} ${RERUN[*]}"
    echo "Rerun log: $OUT/rerun.log"
    DERBYNET_LOAD_LOCK_HELD="$LOCK" \
      "${RUNNER[@]}" -rfE --junitxml="$OUT/rerun-junit.xml" "${RERUN[@]}" \
      >"$OUT/rerun.log" 2>&1 9>&- &
    RUN_PID=$!
    rerun_status=0
    wait "$RUN_PID" || rerun_status=$?
    RUN_PID=
    sed -n '/short test summary info/,$p' "$OUT/rerun.log"
    grep -q 'short test summary info' "$OUT/rerun.log" || tail -n 5 "$OUT/rerun.log"
    echo "rerun exited $rerun_status; log: $OUT/rerun.log; junit: $OUT/rerun-junit.xml"
    tool=0
    said=$(python3 -I "$SERVER_DIR/tools/network_changed.py" outcome \
             "$status" "$veth_count" "$rerun_status" $FLAKE_TICKET) || tool=$?
    if [ "$tool" = 0 ] && [[ "$said" =~ $'\n'exit\ ([0-9]+)$ ]]; then
      echo "${said%$'\n'exit *}"
      final=${BASH_REMATCH[1]}
    else
      echo "network_changed.py outcome failed (exit $tool): the status stays pytest's."
    fi
  elif [ "$tool" = 1 ]; then
    echo "$reason"
  else
    echo "network_changed.py rerun-args failed (exit $tool): no rerun, and the" \
         "status stays pytest's."
  fi
fi
if [ "$final" = "$status" ]; then
  echo "pytest exited $status; log: $OUT/pytest.log; junit: $OUT/junit.xml"
else
  echo "pytest exited $status; log: $OUT/pytest.log; junit: $OUT/junit.xml;" \
       "the script exits $final (D122)"
fi
exit "$final"
