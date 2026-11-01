#! /bin/bash
#
# Builds the PHP server image from the current checkout and runs the shell test
# suite against it, all inside containers.  GitHub Actions and local Linux runs
# use this same script.  See testing/README.md.
#
# Usage: testing/docker/run-suite.sh [step ...]
#
# Steps, in the order "all" runs them:
#   harness  Build the image holding the test tools (ant, jq, xmllint, node, Chrome)
#   assets   Build the gitignored files the PHP pages need (banner.inc, timer.php, ...)
#   server   Build the server image from installer/docker/Dockerfile and start it
#   timeout  Raise the PHP and nginx timeouts, for test-extended-scheduling.sh
#   autoincrement
#            Make the server's SQLite tables never reuse an ID, as Django's don't
#   suite    Run test-face-detection.sh, js-syntax-check.sh, exercise-one-database.sh
#   parity   Run exercise-one-database.sh as a leg of the parity gate (#37), from
#            a copy of testing/ in $DERBYNET_PARITY_DIR, and record each step's
#            transcript in $DERBYNET_PARITY_DIR/steps.  With DERBYNET_PARITY_URL
#            set, run against that server on the host's network (Django)
#            instead of the server container.  See server/tools/parity_gate.py.
#   logs     Print the server container's log
#   clean    Remove the server container and its data volume
#
# With no arguments, runs "all", which cleans up even when a step fails.
#
# DERBYNET_TEST_PREFIX (default "derbynet-test") prefixes the names of the images,
# container, and volume, so concurrent users of one Docker daemon don't collide.
# DERBYNET_TEST_PORT, if set, also publishes the server on that port of localhost,
# for running individual test scripts from the host.
#
# DERBYNET_SHIFT_IDS, if set, makes the "suite" step shift the IDs that the
# database hands out, by creating and deleting throwaway rows after each database
# reset (testing/shift-ids.sh).  Run the "autoincrement" step too, or the IDs
# that SQLite reuses won't shift.  "all" runs it when DERBYNET_SHIFT_IDS is set.

set -e -E -o pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX="${DERBYNET_TEST_PREFIX:-derbynet-test}"
HARNESS_IMAGE="$PREFIX-harness"
SERVER_IMAGE="$PREFIX-server"
SERVER="$PREFIX-server"
VOLUME="$PREFIX-data"

# The harness shares the server's network namespace, so the suite can use the
# URL http://localhost exactly as it does on a developer machine.  It runs as the
# invoking user so the files it writes in the checkout aren't owned by root.
harness_run() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e DERBYNET_SHIFT_IDS \
         -v "$ROOT:/derbynet" "$@"
}

step_harness() {
  docker build -t "$HARNESS_IMAGE" -f "$ROOT/testing/docker/Dockerfile" \
         "$ROOT/testing/puppeteer"
}

step_assets() {
  # The "generated" target, which timer-jar depends on, copies the quick
  # reference PDF into place, but that PDF needs LibreOffice to build.  Satisfy
  # the copy with an empty file, and don't leave it where the image would pick
  # it up.
  mkdir -p "$ROOT/docs/dist"
  local stub="$ROOT/docs/dist/Quick Reference.pdf"
  local made_stub=
  [ -e "$stub" ] || { touch "$stub"; made_stub=1; }
  local status=0
  harness_run "$HARNESS_IMAGE" ant banner generated-version.inc \
         generated-build-date.inc generated-commit-hash.inc timer-in-browser \
    || status=$?
  if [ -n "$made_stub" ]; then
    rm -f "$stub" "$ROOT/website/quickref.pdf"
  fi
  return $status
}

step_server() {
  step_clean
  docker build -t "$SERVER_IMAGE" -f "$ROOT/installer/docker/Dockerfile" "$ROOT"
  docker volume create "$VOLUME" >/dev/null
  local publish=()
  [ -z "$DERBYNET_TEST_PORT" ] || publish=(-p "127.0.0.1:$DERBYNET_TEST_PORT:80")
  docker run -d --name "$SERVER" "${publish[@]}" -v "$VOLUME:/var/lib/derbynet" \
         "$SERVER_IMAGE" >/dev/null
  for _ in $(seq 1 60); do
    if harness_run --network "container:$SERVER" "$HARNESS_IMAGE" \
           curl -sf --location -o /dev/null http://localhost/index.php ; then
      return 0
    fi
    sleep 1
  done
  echo "index.php never answered 200" >&2
  docker logs "$SERVER" >&2
  return 1
}

step_timeout() {
  docker exec "$SERVER" set-timeout.sh
}

step_autoincrement() {
  # PHP's SQLite tables declare "INTEGER PRIMARY KEY", which reuses the ID of a
  # deleted last row.  With AUTOINCREMENT an ID is never handed out twice, which
  # is how Django's tables behave (ADR-12).  The tables are created from these
  # scripts, so change them before the suite creates the database.  A line that
  # already says AUTOINCREMENT stays as it is, so the step can run twice.
  docker exec "$SERVER" sh -c \
         "sed -i '/AUTOINCREMENT/! s/INTEGER PRIMARY KEY/INTEGER PRIMARY KEY AUTOINCREMENT/' /var/www/html/sql/sqlite/*.inc"
  # php-fpm may have the scripts cached.  See set-timeout.sh about the signal.
  docker exec "$SERVER" pkill -USR2 -f 'php-fpm.*master process'
}

step_suite() {
  # test-face-detection.sh needs the PHP CLI, which only the server image has.
  # It runs first so that the log ends with the output of the main suite.
  # Start from nothing, so that a second run doesn't nest a copy of testing/
  # inside the first one's.
  docker exec "$SERVER" rm -rf /tmp/derbynet
  docker exec "$SERVER" mkdir /tmp/derbynet
  docker cp "$ROOT/testing" "$SERVER:/tmp/derbynet/testing"
  docker exec "$SERVER" ln -s /var/www/html /tmp/derbynet/website
  docker exec "$SERVER" bash /tmp/derbynet/testing/test-face-detection.sh

  harness_run --network "container:$SERVER" "$HARNESS_IMAGE" bash -c '
    set -e -o pipefail
    testing/js-syntax-check.sh
    testing/exercise-one-database.sh http://localhost ez 2>&1 | tee testing/suite.log
  '
}

step_parity() {
  local dir="${DERBYNET_PARITY_DIR:?set DERBYNET_PARITY_DIR to a directory for the leg}"
  local network=(--network "container:$SERVER") url=http://localhost
  if [ -n "$DERBYNET_PARITY_URL" ]; then
    network=(--network host)
    url="$DERBYNET_PARITY_URL"
  fi
  # Each leg has its own copy of testing/, since the suite keeps its cookies and
  # transcript next to the scripts.
  rm -rf "$dir"
  mkdir -p "$dir/steps"
  cp -R "$ROOT/testing" "$dir/testing"
  rm -f "$dir"/testing/*.curl
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e DERBYNET_SHIFT_IDS \
         -e DERBYNET_BACKEND -e PARITY_DIR=/leg/steps -v "$dir:/leg" -w /leg \
         "${network[@]}" "$HARNESS_IMAGE" bash -c '
    set -o pipefail
    testing/exercise-one-database.sh "$0" ez 2>&1 | tee steps/suite.log
  ' "$url"
}

step_logs() {
  docker logs "$SERVER" 2>&1
}

step_clean() {
  docker rm -f "$SERVER" >/dev/null 2>&1 || true
  docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
}

step_all() {
  trap step_clean EXIT
  step_harness
  step_assets
  step_server
  step_timeout
  [ -z "$DERBYNET_SHIFT_IDS" ] || step_autoincrement
  step_suite
}

if [ $# -eq 0 ]; then
  set -- all
fi
for step in "$@"; do
  case "$step" in
    harness|assets|server|timeout|autoincrement|suite|parity|logs|clean|all) "step_$step" ;;
    *) echo "Unknown step: $step" >&2; exit 2 ;;
  esac
done
