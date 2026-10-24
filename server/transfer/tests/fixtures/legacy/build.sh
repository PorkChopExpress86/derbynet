#! /bin/bash
#
# Regenerates the legacy DerbyNet fixtures that the importer tests read, from
# the PHP server.  Run it from a regular clone (not a git worktree), on a
# machine with docker, bash and python3:
#
#   server/transfer/tests/fixtures/legacy/build.sh [--keep-server]
#
# It builds the harness and server images and starts a server with a fresh data
# volume, by calling the steps of testing/docker/run-suite.sh.  Then it
#
#   v10      fills an event with scenario.sh and copies out its database, its
#            photo folders, and query=snapshot.get&photos
#   v9 ...   writes the same rows at schema 9, 7, 5 and 1 with downgrade.php,
#            under the server's PHP CLI and a copy of website/ pinned to that
#            version
#   checks   opens each database in the PHP server, runs
#            action=database.execute&script=update-schema, and records
#            query=database.check.  The v7 file after that upgrade is the
#            v7-upgraded fixture.
#
# Each database is committed as SQL text (`<name>.sql`), which the tests load
# into a SQLite file.  The server is removed at the end.
#
# DERBYNET_TEST_PREFIX (default "derbynet-legacy") names the images, container
# and volume, as it does for run-suite.sh.

set -e -E -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../../.." && pwd)"
REL="${HERE#"$ROOT"/}"
export DERBYNET_TEST_PREFIX="${DERBYNET_TEST_PREFIX:-derbynet-legacy}"
RUN_SUITE="$ROOT/testing/docker/run-suite.sh"
SERVER="$DERBYNET_TEST_PREFIX-server"
HARNESS_IMAGE="$DERBYNET_TEST_PREFIX-harness"
WORK="$HERE/.work"
DATA=/var/lib/derbynet

# Runs a command in the harness image on the server's network, from the root of
# the checkout, with BASE_URL set and testing/common.sh available.
harness() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e BASE_URL=http://localhost \
         -e PASSWORDS_FILE=testing/default.passwords \
         --network "container:$SERVER" -v "$ROOT:/derbynet" -w /derbynet \
         "$HARNESS_IMAGE" bash -c "set -e -o pipefail; NOANNOUNCE=1 source testing/common.sh; $1"
}

keep=
for arg in "$@"; do
  case "$arg" in
    --keep-server) keep=1 ;;
    *) echo "usage: build.sh [--keep-server]" >&2; exit 2 ;;
  esac
done

cleanup() {
  # testing/common.sh keeps its cookie jar and curl logs next to the script
  # that sources it, which is the checkout's root for harness().
  rm -rf "$WORK" "$HERE"/*.curl "$ROOT"/*.curl
  [ -n "$keep" ] || "$RUN_SUITE" clean
}
trap cleanup EXIT

rm -rf "$WORK"
mkdir -p "$WORK/photos"
"$RUN_SUITE" harness assets server timeout

# Three small, distinct JPEGs, so that a photo's MD5 names its racer.
for spec in head-101:200,30,30 head-111:30,200,30 car-101:30,30,200; do
  docker exec "$SERVER" php -r "
    \$i = imagecreatetruecolor(16, 16);
    imagefill(\$i, 0, 0, imagecolorallocate(\$i, ${spec#*:}));
    imagejpeg(\$i);" > "$WORK/photos/${spec%%:*}.jpg"
done

echo "== v10" >&2
harness "bash $REL/scenario.sh http://localhost $REL/.work/photos"
EVENT_DIR="$(docker exec "$SERVER" sh -c "dirname $DATA/*/testdb/derbynet.sqlite3")"
harness "user_login_coordinator; curl -s -b \$COOKIES_CURL 'http://localhost/action.php?query=snapshot.get&photos' > $REL/.work/v10.snapshot"
docker cp "$SERVER:$EVENT_DIR/derbynet.sqlite3" "$WORK/v10.sqlite3"
rm -rf "$HERE/v10-photos"
mkdir -p "$HERE/v10-photos"
docker cp "$SERVER:$EVENT_DIR/racers" "$HERE/v10-photos/racers"
docker cp "$SERVER:$EVENT_DIR/cars" "$HERE/v10-photos/cars"
mv "$WORK/v10.snapshot" "$HERE/v10.snapshot"

echo "== older schemas" >&2
docker exec "$SERVER" rm -rf /tmp/fixtures
docker exec "$SERVER" mkdir /tmp/fixtures
docker cp "$WORK/v10.sqlite3" "$SERVER:/tmp/fixtures/v10.sqlite3"
docker cp "$HERE/downgrade.php" "$SERVER:/tmp/fixtures/downgrade.php"
for version in 9 7 5 1; do
  docker exec "$SERVER" sh -c "
    set -e
    rm -rf /tmp/web && cp -r /var/www/html /tmp/web
    sed -i 's/return 10;/return $version;/' /tmp/web/inc/schema_version.inc
    cd /tmp/web && php /tmp/fixtures/downgrade.php /tmp/fixtures/v10.sqlite3 /tmp/fixtures/v$version.sqlite3 $version"
  docker cp "$SERVER:/tmp/fixtures/v$version.sqlite3" "$WORK/v$version.sqlite3"
done

echo "== checks" >&2
mkdir -p "$HERE/checks"
for name in v10 v9 v7 v5 v1; do
  docker exec "$SERVER" sh -c "
    mkdir -p $DATA/fixtures && cp /tmp/fixtures/$name.sqlite3 $DATA/fixtures/$name.sqlite3 &&
    chown -R www-data $DATA/fixtures 2>/dev/null || chmod -R a+rwX $DATA/fixtures"
  harness "user_login_coordinator
    curl_postj action.php 'action=setup.nodata&connection_string=sqlite:$DATA/fixtures/$name.sqlite3&dbuser=&dbpass=' | check_jsuccess
    user_login_coordinator
    curl_postj action.php 'action=database.execute&script=update-schema' | check_jsuccess
    curl -s -b \$COOKIES_CURL 'http://localhost/action.php?query=database.check' | jq -S . > $REL/checks/$name.json"
done
docker cp "$SERVER:$DATA/fixtures/v7.sqlite3" "$WORK/v7-upgraded.sqlite3"

for name in v10 v9 v7 v7-upgraded v5 v1; do
  python3 -c '
import sqlite3, sys
with open(sys.argv[2], "w") as out:
    for line in sqlite3.connect(sys.argv[1]).iterdump():
        out.write(line + "\n")
' "$WORK/$name.sqlite3" "$HERE/$name.sql"
done

echo "fixtures: $(cd "$HERE" && ls *.sql *.snapshot checks/*.json | tr '\n' ' ')" >&2
