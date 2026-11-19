#! /bin/bash
#
# Starts a long command in a session and a systemd scope of its own, so it
# outlives the agent that starts it and the editor it runs in, and names the
# pid to wait on and stop.
#
# Usage: server/tools/detach.sh LOG COMMAND [ARGUMENT ...]
#        server/tools/detach.sh --wait LOG [SECONDS]
#
#   tools/detach.sh ~/.cache/derbynet-drafts/N/chain.log \
#     systemd-inhibit --what=sleep:idle --who=derbynet-N --why="#N hand-off" \
#     bash ~/.cache/derbynet-drafts/N/chain.sh
#
# The first form refuses a LOG that exists, writes COMMAND's output to it,
# and prints `session PID`: COMMAND's session and process group, whose
# leader lives until COMMAND ends.  Stop it with `kill -TERM -- -PID`.  When
# COMMAND ends, its status goes on the log's last line as
# `detach.sh: exit N`.
#
# `setsid CMD &` then `$!` names a pid that is gone at once in the Bash
# tool's shell, which has job control on: each background job leads its own
# process group, so setsid forks and its parent exits.  #391's implementer
# reported such a pid for its queued hand-off, and the lead's #367 waiter
# said "chain ended" on one while the chain still waited for the load lock.
#
# A Claude Code restart stops every process in the editor's systemd scope
# (app-com.microsoft.VSCode-PID.scope), setsid's sessions included: the
# restart of 2026-11-17 ended the detached load chains with it (#391 retro).
# So COMMAND runs under `systemd-run --user --scope`, in a scope of its own
# that stopping the starter's scope leaves running, and the first form names
# that scope.  It expands no `$VAR` in COMMAND, as systemd-run does by
# default (`$$` reached the command as `$`).  Without a user systemd manager
# (a container, or a shell with no user bus) it says so on stderr, and
# COMMAND ends with the starter's scope.
#
# The pid it prints is one a lead will pass to `kill -- -PID`, so the first
# form checks it before printing it: the process must lead its own session
# and be this script's `--run` shell, or be gone with COMMAND's exit line in
# LOG, or the first form prints no pid and exits 1.  A
# mutant that wrote `$PPID` on the session line named systemd --user, the
# manager of the whole desktop session, and the test that stopped that group
# ended the session twice on 2026-11-17 (#391 retro, tests/test_detach.py).
#
# The second form waits up to SECONDS (160 by default: the Bash tool moves a
# foreground command to the background after 170 to 180 s, #282) and prints
# one line:
#
#   ended: exit N    LOG has the exit line; exits 0 when N is 0, else 1
#   running          the session leader is alive with no exit line; exits 3
#   died             the leader is gone, or its pid now names another
#                    process, and LOG has no exit line, as after a kill;
#                    exits 4
#
# Neither form traps a signal: stopping the group stops the leader too,
# and the log then has no exit line.

if [ "${1:-}" = "--run" ]; then
  # The session leader: `setsid -f` below forks once, and that child calls
  # setsid(2) and execs systemd-run (when there is a scope), which execs this
  # script in the same process, so `$$` is the session's pid.  `$PPID` is
  # not: setsid's parent has exited, so the parent is a subreaper, here
  # systemd --user.
  shift
  echo "session $$"
  echo "cwd $PWD"
  echo "head $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "args $*"
  echo "cgroup $(cut -d: -f3 /proc/self/cgroup 2>/dev/null)"
  "$@"
  status=$?
  echo "detach.sh: exit $status"
  exit "$status"
fi

usage() {
  echo "usage: detach.sh LOG COMMAND [ARGUMENT ...]" >&2
  echo "       detach.sh --wait LOG [SECONDS]" >&2
  exit 2
}

session_of() {
  sed -n '1s/^session \([0-9][0-9]*\)$/\1/p' "$1" 2>/dev/null
}

# Returns 0 when PID leads its own session as this script's `--run` shell
# (/proc/PID/stat's session field is PID and its comm is this script's
# name), 1 when no process has that pid, and 2 when another process has it.
# comm keeps only the first 15 characters of a longer name
# (tests/test_detach.py's long-named copy).
me=${0##*/}
me=${me:0:15}
leader_state() {
  local stat comm sid
  stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  comm=${stat#*(}
  comm=${comm%)*}
  read -r _ _ _ sid _ <<<"${stat##*) }"
  [ "$sid" = "$1" ] && [ "$comm" = "$me" ] && return 0
  return 2
}

if [ "${1:-}" = "--wait" ]; then
  [ $# -ge 2 ] && [ $# -le 3 ] || usage
  log=$2
  seconds=${3:-160}
  case "$seconds" in '' | *[!0-9]*) usage ;; esac
  leader=$(session_of "$log")
  if [ -z "$leader" ]; then
    echo "detach.sh: $log does not start with a detach.sh session line." >&2
    exit 2
  fi
  deadline=$(( $(date +%s) + seconds ))
  while :; do
    # The leader is checked before the log is read, so an exit line written
    # between the two reads is still seen.
    alive=0
    leader_state "$leader" && alive=1
    last=$(tail -n 1 "$log")
    case "$last" in
      "detach.sh: exit "*)
        echo "ended: ${last#detach.sh: }"
        [ "$last" = "detach.sh: exit 0" ] && exit 0
        exit 1
        ;;
    esac
    if [ "$alive" = 0 ]; then
      echo "died: session $leader is gone and $log has no exit line"
      exit 4
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "running: session $leader"
      exit 3
    fi
    sleep 1
  done
fi

[ $# -ge 2 ] || usage
log=$1
shift
if ! command -v setsid >/dev/null; then
  echo "detach.sh: it needs setsid(1) from util-linux." >&2
  exit 2
fi
if ! (set -o noclobber; : >"$log") 2>/dev/null; then
  echo "detach.sh: $log exists or cannot be written; name a new file, since" \
       "an old exit line would end the wait at once." >&2
  exit 2
fi
scope=()
where=", with no scope of its own"
if command -v systemd-run >/dev/null &&
   systemctl --user show-environment >/dev/null 2>&1; then
  unit="derbynet-detach-$$-$(date +%s).scope"
  scope=(systemd-run --user --scope --quiet --collect --expand-environment=no
         "--unit=$unit")
  where=" in $unit"
else
  echo "detach.sh: no user systemd manager: COMMAND stops with this shell's" \
       "scope, as in a Claude Code restart." >&2
fi
setsid -f "${scope[@]}" "$0" --run "$@" >>"$log" 2>&1 </dev/null
for _ in $(seq 100); do
  leader=$(session_of "$log")
  if [ -n "$leader" ]; then
    # A short COMMAND may have ended already, so a gone leader passes when
    # the log holds its exit line, read after the check as in --wait.  A pid
    # that names another process never passes.
    leader_state "$leader"
    case "$?:$(tail -n 1 "$log")" in
      0:* | "1:detach.sh: exit "*) ;;
      *)
        echo "detach.sh: $log names pid $leader, which is not the session" \
             "leader this script started; it prints no pid to stop." >&2
        exit 1
        ;;
    esac
    echo "session $leader$where: wait with \`$0 --wait $log\`; stop it" \
         "with \`kill -TERM -- -$leader\`"
    exit 0
  fi
  sleep 0.1
done
echo "detach.sh: no session line in $log after 10 s." >&2
exit 1
