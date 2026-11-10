#! /bin/bash
#
# Runs one script from testing/ against a throwaway PHP server, from a regular
# checkout or a git worktree, and removes the server afterwards.  Use it to see
# what PHP does.  See testing/README.md.
#
# Usage: testing/docker/php-scratch.sh [--no-event] [--keep] <script> [argument ...]
#
# <script> is a path under testing/, such as test-points-racing.sh.  The base
# URL, http://localhost, is its first argument, followed by any others.  It
# runs under bash, so a new script needs no execute bit.
#
# A fresh PHP server has no event, so every login fails until setup.nodata
# creates one.  The script does that first (ez-new=testdb), unless --no-event
# is given.  Pass --no-event for a script that sets the event up itself, such as
# exercise-one-database.sh, because a second setup fails with already_exists.
#
# --keep leaves the server running after the script, on a port of localhost
# that it prints with the command that removes it, so a browser can measure
# PHP's page in the state the script left.  #247 and #250 each ran the
# containers by hand to keep one, and each lost tries to the cwd, a login the
# next harness run no longer had, and default.passwords (#250 retro).
#
# The images must exist already: the harness and the server image that
# run-suite.sh builds, named after DERBYNET_TEST_PREFIX (default
# "derbynet-test").  Build them once from a regular clone with
#   testing/docker/run-suite.sh harness assets server
# This script reads them only; it removes its own container and volume.

set -e -E -o pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX="${DERBYNET_TEST_PREFIX:-derbynet-test}"
HARNESS_IMAGE="$PREFIX-harness"
SERVER_IMAGE="$PREFIX-server"
SERVER="$PREFIX-scratch-$$"
VOLUME="$PREFIX-scratch-$$"

CREATE_EVENT=1
KEEP=
while [ $# -gt 0 ]; do
  case "$1" in
    --no-event) CREATE_EVENT= ;;
    --keep) KEEP=1 ;;
    *) break ;;
  esac
  shift
done
[ $# -gt 0 ] || { sed -n '2,/^[^#]/{/^#/p}' "$0" >&2; exit 2; }

for image in "$HARNESS_IMAGE" "$SERVER_IMAGE"; do
  docker image inspect "$image" >/dev/null 2>&1 || {
    echo "No image $image.  From a regular clone, run:" >&2
    echo "  DERBYNET_TEST_PREFIX=$PREFIX testing/docker/run-suite.sh harness assets server" >&2
    exit 1
  }
done

clean() {
  docker rm -f "$SERVER" >/dev/null 2>&1 || true
  docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
}
# A kept server is named even when the script fails, so it can be removed.
kept() {
  echo "PHP is at http://$(docker port "$SERVER" 80/tcp 2>/dev/null | head -n 1)/" >&2
  echo "Remove it with: docker rm -f $SERVER && docker volume rm $VOLUME" >&2
}
if [ -n "$KEEP" ]; then trap kept EXIT; else trap clean EXIT; fi

# The harness shares the server's network namespace, so the script reaches the
# server at http://localhost as it does on a developer machine.
harness_run() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --network "container:$SERVER" \
         -v "$ROOT:/derbynet" "$HARNESS_IMAGE" "$@"
}

docker volume create "$VOLUME" >/dev/null
PUBLISH=()
[ -z "$KEEP" ] || PUBLISH=(-p 127.0.0.1::80)
docker run -d --name "$SERVER" "${PUBLISH[@]}" -v "$VOLUME:/var/lib/derbynet" \
       "$SERVER_IMAGE" >/dev/null
for _ in $(seq 1 60); do
  harness_run curl -sf --location -o /dev/null http://localhost/index.php && break
  sleep 1
done
docker exec "$SERVER" set-timeout.sh >/dev/null

if [ -n "$CREATE_EVENT" ]; then
  harness_run curl -sf -o /dev/null -d "action=setup.nodata&ez-new=testdb" \
         http://localhost/action.php
fi

SCRIPT="$1"
shift
harness_run bash -c 'set -e -o pipefail; cd /derbynet && bash testing/"$0" http://localhost "$@"' \
       "$SCRIPT" "$@"
