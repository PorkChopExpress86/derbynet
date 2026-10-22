#! /bin/bash
#
# Regenerates every golden fixture under server/tests/golden from a clean PHP
# server.  One command, run from a regular checkout (not a git worktree) on a
# machine with docker and bash:
#
#   server/tests/golden/capture.sh [--check] [--keep-server] [--engine-only | --http-only]
#
# It builds the harness and server images and starts a server with a fresh data
# volume, by calling the steps of testing/docker/run-suite.sh, then
#
#   engine  runs capture/capture-engine.php under the server image's PHP CLI,
#           which writes engine/
#   http    runs capture/capture-http.sh in the harness image on the server's
#           network, which writes http/
#
# and removes the server.  See README.md for what each directory holds.
#
#   --check        afterwards, fail if anything under server/tests/golden differs
#                  from what git has (the CI job uses this)
#   --keep-server  leave the server container and its volume
#   --engine-only, --http-only   run one half, for development
#
# DERBYNET_TEST_PREFIX (default "derbynet-golden") names the images, container and
# volume, as it does for run-suite.sh.

set -e -E -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
GOLDEN="$ROOT/server/tests/golden"
export DERBYNET_TEST_PREFIX="${DERBYNET_TEST_PREFIX:-derbynet-golden}"
RUN_SUITE="$ROOT/testing/docker/run-suite.sh"
SERVER="$DERBYNET_TEST_PREFIX-server"
HARNESS_IMAGE="$DERBYNET_TEST_PREFIX-harness"

check=
keep=
do_engine=1
do_http=1
for arg in "$@"; do
  case "$arg" in
    --check) check=1 ;;
    --keep-server) keep=1 ;;
    --engine-only) do_http= ;;
    --http-only) do_engine= ;;
    *) echo "usage: capture.sh [--check] [--keep-server] [--engine-only | --http-only]" >&2; exit 2 ;;
  esac
done

step_server() {
  "$RUN_SUITE" harness assets server timeout
  echo "php: $(docker exec "$SERVER" php -r 'echo PHP_VERSION;')" \
       "on $(docker exec "$SERVER" sed -n 's/^PRETTY_NAME=//p' /etc/os-release)" >&2
}

step_engine() {
  echo "== engine" >&2
  docker exec "$SERVER" rm -rf /tmp/golden-capture /tmp/golden-out
  docker cp "$HERE/capture" "$SERVER:/tmp/golden-capture"
  docker exec -w /var/www/html "$SERVER" \
         php -d serialize_precision=-1 /tmp/golden-capture/capture-engine.php /tmp/golden-out
  local tmp
  tmp="$(mktemp -d)"
  docker cp "$SERVER:/tmp/golden-out/engine" "$tmp/engine"
  rm -rf "$GOLDEN/engine"
  mv "$tmp/engine" "$GOLDEN/engine"
  rmdir "$tmp"
}

step_http() {
  echo "== http" >&2
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
         --network "container:$SERVER" -v "$ROOT:/derbynet" "$HARNESS_IMAGE" \
         bash /derbynet/server/tests/golden/capture/capture-http.sh /derbynet/server/tests/golden
}

step_clean() {
  [ -n "$keep" ] || "$RUN_SUITE" clean
}

# With --check, compare right after each half, so an engine difference is
# reported without waiting for the HTTP capture (or being hidden by its failure).
check_fixtures() {
  local changed
  changed="$(git -C "$ROOT" status --porcelain -- server/tests/golden)"
  if [ -n "$changed" ]; then
    echo "The capture differs from the committed fixtures:" >&2
    echo "$changed" | head -n 40 >&2
    git -C "$ROOT" --no-pager diff --stat -- server/tests/golden | tail -n 40 >&2
    git -C "$ROOT" --no-pager diff -U1 -- server/tests/golden | head -n 120 >&2
    exit 1
  fi
  echo "The capture matches the committed fixtures." >&2
}

trap step_clean EXIT
step_server
if [ -n "$do_engine" ]; then
  step_engine
  [ -z "$check" ] || check_fixtures
fi
[ -z "$do_http" ] || step_http

echo "== summary" >&2
[ ! -f "$GOLDEN/engine/summary.txt" ] || cat "$GOLDEN/engine/summary.txt" >&2
echo "files: $(find "$GOLDEN" -type f | wc -l), size: $(du -sh "$GOLDEN" | cut -f1)" >&2

[ -z "$check" ] || check_fixtures
