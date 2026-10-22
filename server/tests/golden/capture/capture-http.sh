#! /bin/bash
#
# Drives the PHP server over HTTP and writes down every exchange a scenario asks
# for.  Run inside the test harness container, on the server's network, from the
# repository root mounted at /derbynet; capture.sh does that.
#
#   server/tests/golden/capture/capture-http.sh <outdir>
#
# This file sources testing/common.sh, so the scenarios can call the suite's own
# helpers (user_login_coordinator, run_heat, rankid_of, and the scripts in
# testing/).  It adds
#
#   capture_scenario <name>    start the transcript http/<name>.http
#   capture <name> <request>   send a request, append the exchange to the transcript
#
# where <request> is "GET path?query", "POST path form-encoded-body", or
# "POST path?query form-encoded-body".  Set CAPTURE_UA before a call to send that
# User-Agent; otherwise requests send "golden-capture".  After a call, the
# response body is in $CAPTURE_BODY and its status in $CAPTURE_STATUS.
#
# Raw transcripts land in a work directory, then normalize.mjs writes the files
# under <outdir>/http with every volatile value masked.

set -e -E -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
OUTDIR="${1:?usage: capture-http.sh <outdir>}"
OUTDIR="$(cd "$OUTDIR" && pwd)"

BASE_URL="${BASE_URL:-http://localhost}"
RECORDER_PORT=8081
RAW="$(mktemp -d /tmp/golden-raw.XXXXXX)"
# GOLDEN_KEEP_RAW=<dir> keeps the raw, unnormalized transcripts there, to debug a rule.
trap 'kill $RECORDER_PID 2>/dev/null || true; [ -z "$GOLDEN_KEEP_RAW" ] || cp -r "$RAW/." "$GOLDEN_KEEP_RAW/"; rm -rf "$RAW"' EXIT

export NOANNOUNCE=1
export PASSWORDS_FILE="$ROOT/testing/default.passwords"
# common.sh puts its scratch files next to $0.
source "$ROOT/testing/common.sh"
OUTPUT_CURL=/dev/null
DEBUG_CURL="$RAW/.debug.curl"
COOKIES_CURL="$RAW/.cookies.curl"

CAPTURE_FILE=
CAPTURE_BODY="$RAW/.last-body"
CAPTURE_STATUS=
declare -A CAPTURE_SEQS   # exchanges written so far to each transcript

# capture_scenario <name>: write to http/<name>.http.  Naming a transcript again
# picks it up where it left off.
capture_scenario() {
  CAPTURE_FILE="$RAW/$1.http"
  mkdir -p "$(dirname "$CAPTURE_FILE")"
  if [ -z "${CAPTURE_SEQS[$CAPTURE_FILE]:-}" ]; then
    : > "$CAPTURE_FILE"
    CAPTURE_SEQS[$CAPTURE_FILE]=0
    echo "  scenario $1" >&2
  fi
}

# capture <name> <request>
capture() {
  local name="$1" request="$2"
  local method="${request%% *}" rest="${request#* }"
  local path="${rest%% *}" body=
  if [ "$rest" != "$path" ]; then body="${rest#* }"; fi
  case "$body" in *$'\n'*) echo "capture: newline in request body of $name" >&2; return 1 ;; esac
  [ -n "$CAPTURE_FILE" ] || { echo "capture: no scenario" >&2; return 1; }

  local ua="${CAPTURE_UA:-golden-capture}"
  local headers="$RAW/.last-headers"
  local args=(-s -A "$ua" -b "$COOKIES_CURL" -c "$COOKIES_CURL" -D "$headers" -o "$CAPTURE_BODY")
  case "$method" in
    GET) ;;
    POST) args+=(-d "$body") ;;
    *) echo "capture: unsupported method $method" >&2; return 1 ;;
  esac
  curl "${args[@]}" "$BASE_URL/$path"

  CAPTURE_STATUS="$(head -n 1 "$headers" | tr -d '\r' | cut -d' ' -f2)"
  local ctype location cache_control
  ctype="$(grep -i '^content-type:' "$headers" | head -n 1 | tr -d '\r' | cut -d' ' -f2- || true)"
  location="$(grep -i '^location:' "$headers" | head -n 1 | tr -d '\r' | cut -d' ' -f2- || true)"
  cache_control="$(grep -i '^cache-control:' "$headers" | head -n 1 | tr -d '\r' | cut -d' ' -f2- || true)"
  local seq=$((CAPTURE_SEQS[$CAPTURE_FILE] + 1))
  CAPTURE_SEQS[$CAPTURE_FILE]=$seq
  {
    printf '### %03d %s\n' "$seq" "$name"
    printf 'request: %s %s\n' "$method" "$path"
    [ -z "${CAPTURE_UA:-}" ] || printf 'user-agent: %s\n' "$CAPTURE_UA"
    [ "$method" != POST ] || printf 'request-body: %s\n' "$body"
    printf 'status: %s\n' "$CAPTURE_STATUS"
    [ -z "$ctype" ] || printf 'content-type: %s\n' "$ctype"
    [ -z "$location" ] || printf 'location: %s\n' "$location"
    grep -i '^set-cookie:' "$headers" | tr -d '\r' | cut -d' ' -f2- | sed 's/^/set-cookie: /' || true
    [ -z "$cache_control" ] || printf 'cache-control: %s\n' "$cache_control"
    printf 'length: %s\nbody:\n' "$(wc -c < "$CAPTURE_BODY")"
    cat "$CAPTURE_BODY"
    printf '\n'
  } >> "$CAPTURE_FILE"
}

# use_session <name>: switch to the named login session (a separate cookie jar),
# so one scenario can act as the coordinator and the timer at once.
use_session() {
  COOKIES_CURL="$RAW/.cookies.$1"
}

# capture_ok <name> <request>: like capture, but the scenario stops unless the
# server reported success.  For steps that set up state.
capture_ok() {
  capture "$@"
  if jq -e '.outcome.summary == "success"' "$CAPTURE_BODY" >/dev/null 2>&1 ||
     grep -q '<success' "$CAPTURE_BODY"; then
    return 0
  fi
  echo "capture_ok: $1 did not succeed:" >&2
  head -c 600 "$CAPTURE_BODY" >&2
  return 1
}

# post <request-body>: an action without recording it; fails unless it succeeds.
post_ok() {
  curl_postj action.php "$1" | check_jsuccess
}

# Reset the server's database to empty tables, as the suite does between scripts.
reset_database() {
  "$ROOT/testing/reset-database.sh" "$BASE_URL" >/dev/null
}

# race_round [salt]: with the coordinator and timer sessions set up, race every
# heat of the round now racing, until racing stops or the round changes.  A
# racer's time depends only on car number, lane and the salt, so a run is the
# same every time: 2.000 to 2.999 seconds.  $RACED_HEATS says how many heats ran.
#   RACE_TIME_EXPR  a jq expression for a racer's time in thousandths of a second,
#                   over a poll.coordinator racer (.carnumber, .lane) and $salt
#   RACE_MAX_HEATS  stop after this many heats
race_round() {
  local salt="${1:-0}" roundid racing fields
  local expr="${RACE_TIME_EXPR:-2000 + ((.carnumber * 37 + .lane * 11 + \$salt) % 1000)}"
  local prog='.racers | map("lane\(.lane)=\((('"$expr"') / 1000))") | join("&")'
  RACED_HEATS=0
  use_session coord
  roundid="$(curl_getj "action.php?query=poll.coordinator" | jq '.["current-heat"].roundid')"
  while [ "$RACED_HEATS" -lt "${RACE_MAX_HEATS:-100000}" ]; do
    use_session coord
    curl_getj "action.php?query=poll.coordinator" > "$RAW/.race-poll"
    racing="$(jq '.["current-heat"].now_racing' "$RAW/.race-poll")"
    [ "$racing" = true ] || break
    [ "$(jq '.["current-heat"].roundid' "$RAW/.race-poll")" = "$roundid" ] || break
    fields="$(jq -r --argjson salt "$salt" "$prog" "$RAW/.race-poll")"
    use_session timer
    curl_post action.php "action=timer-message&message=STARTED" | check_success
    curl_post action.php "action=timer-message&message=FINISHED&$fields" | check_success
    RACED_HEATS=$((RACED_HEATS + 1))
  done
  use_session coord
}

# set_checkbox <setting> <0|1>: settings.write for a checkbox setting.
set_checkbox() {
  if [ "$2" = 1 ]; then
    post_ok "action=settings.write&$1-checkbox=1&$1=1"
  else
    post_ok "action=settings.write&$1-checkbox=1"
  fi
}

# standings_matrix <transcript>: the standings, the awards and the full export
# under every scoring setting, as the race now stands.  Records query=standings
# and query=award.list for scoring 0, 1, 2 (average, drop slowest, fastest) with
# times and with points, with subgroups on and off; query=award.list with
# one-trophy-per on and off and n-rank-trophies at 0, 1 and 3; and
# export-json.php for the default settings.  Leaves the settings at their defaults.
standings_matrix() {
  capture_scenario "$1"
  use_session coord
  user_login_coordinator
  local points scoring subgroups label one n
  local saved
  saved="$(curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/action.php?query=settings.list")"
  capture "as-the-script-left-it export-json" "GET export-json.php"
  capture "as-the-script-left-it standings" "GET action.php?query=standings"
  capture "as-the-script-left-it award.list" "GET action.php?query=award.list"
  standings_by_car "$RAW/$1.by-car.json"
  for points in 0 1; do
    for scoring in 0 1 2; do
      for subgroups in 0 1; do
        post_ok "action=settings.write&scoring=$scoring"
        set_checkbox use-points $points
        set_checkbox do-use-subgroups $subgroups
        label="points=$points scoring=$scoring subgroups=$subgroups"
        capture "$label standings" "GET action.php?query=standings"
        capture "$label award.list" "GET action.php?query=award.list"
      done
    done
  done
  post_ok "action=settings.write&scoring=0"
  set_checkbox use-points 0
  set_checkbox do-use-subgroups 0
  for one in 0 1; do
    set_checkbox one-trophy-per $one
    capture "one-trophy-per=$one award.list" "GET action.php?query=award.list"
  done
  set_checkbox one-trophy-per 0
  for n in 0 1 3; do
    post_ok "action=settings.write&n-rank-trophies=$n"
    capture "n-rank-trophies=$n award.list" "GET action.php?query=award.list"
  done

  # The scripts that follow expect the settings they left behind.
  post_ok "action=settings.write&scoring=$(echo "$saved" | jq '.settings.scoring // 0')"
  post_ok "action=settings.write&n-rank-trophies=$(echo "$saved" | jq '.settings["n-rank-trophies"] // 0')"
  set_checkbox use-points "$(echo "$saved" | jq '.settings["use-points"] // 0')"
  set_checkbox do-use-subgroups "$(echo "$saved" | jq '.settings["use-subgroups"] // 0')"
  set_checkbox one-trophy-per "$(echo "$saved" | jq '.settings["one-trophy-per"] // 0')"
}

# standings_by_car <file>: the standings ladders with each racerid replaced by
# "<car number> <first> <last>", so a comparison need not depend on row IDs.
standings_by_car() {
  curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/action.php?query=racer.list&order=car" > "$RAW/.racers.json"
  curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/action.php?query=standings" \
    | jq --slurpfile r "$RAW/.racers.json" \
        '.standings | map_values(map(map(. as $id | $r[0].racers | map(select(.racerid == $id))[0] | "\(.carnumber) \(.firstname) \(.lastname)")))' \
    > "$1"
}

# Start the recording proxy for test scripts that must be run unmodified.
start_recorder() {
  node "$HERE/recorder.mjs" "$RECORDER_PORT" 80 "$RAW" >"$RAW/.recorder.log" 2>&1 &
  RECORDER_PID=$!
  for _ in $(seq 1 50); do
    curl -s -o /dev/null "http://127.0.0.1:$RECORDER_PORT/__control/none" && return 0
    sleep 0.1
  done
  echo "recorder did not start" >&2; cat "$RAW/.recorder.log" >&2
  return 1
}

# wait_for_replay_timeout: wait until the server stops counting a replay client
# as connected, 10 s after its last replay.message (REPLAY_TIMEOUT_LIMIT in
# website/inc/replay.inc).  A poll's "replay-state" then does not depend on how
# long the steps before it took.
wait_for_replay_timeout() {
  local i
  for i in $(seq 1 30); do
    [ "$(curl -s -b "$COOKIES_CURL" -c "$COOKIES_CURL" "$BASE_URL/action.php?query=poll&values=replay-state" \
           | jq '.["replay-state"].connected')" = false ] && return 0
    sleep 1
  done
  echo "the replay client still counts as connected after 30 s" >&2
  return 1
}

# record_script <tag> <record-list> <steps 0|1> <script> [args...]
# Runs one script from testing/ through the recording proxy, starting with no
# replay client connected.  The script gets BASE_URL=http://localhost:8081/<tag>.
record_script() {
  local name="$1" record="$2" steps="$3" script="$4"
  shift 4
  local tag="${name//\//__}"   # the proxy's tag is one path segment; __ stands for /
  wait_for_replay_timeout
  echo "  recording $name: $script $*" >&2
  curl -sf "http://127.0.0.1:$RECORDER_PORT/__control/begin?tag=$tag&record=$record&steps=$steps" >/dev/null
  local status=0
  "$ROOT/testing/$script" "http://localhost:$RECORDER_PORT/$tag" "$@" >"$RAW/.script.log" 2>&1 || status=$?
  curl -sf "http://127.0.0.1:$RECORDER_PORT/__control/end?tag=$tag" >/dev/null
  if [ $status -ne 0 ]; then
    echo "$script failed ($status):" >&2
    tail -n 30 "$RAW/.script.log" >&2
    return 1
  fi
}

# run_script <script> [args...]: a script from testing/, not recorded.
run_script() {
  local script="$1"
  shift
  echo "  running $script $*" >&2
  "$ROOT/testing/$script" "$BASE_URL" "$@" >"$RAW/.script.log" 2>&1 || {
    echo "$script failed:" >&2
    tail -n 30 "$RAW/.script.log" >&2
    return 1
  }
}

echo "capture-http: $BASE_URL -> $OUTDIR/http" >&2
rm -rf "$OUTDIR/http"
start_recorder

# GOLDEN_SCENARIOS narrows the run while developing a scenario, e.g. "03-*.sh".
for scenario in "$HERE"/scenarios/${GOLDEN_SCENARIOS:-*.sh}; do
  echo "== $(basename "$scenario")" >&2
  source "$scenario"
done

node "$HERE/normalize.mjs" "$RAW" "$OUTDIR/http"
echo "capture-http: done" >&2
