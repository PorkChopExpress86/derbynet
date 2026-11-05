#! /bin/bash
#
# Runs pytest against a throwaway Postgres 17, the database CI's second matrix
# entry uses, and removes it afterwards.  Some tests pass on SQLite and fail
# only here, such as one that depends on the key order of a jsonb column.
#
# Usage: server/tools/pytest-postgres.sh [pytest argument ...]
#
# Run it from anywhere; it runs `uv run pytest` in server/.  The container
# listens on a free port of localhost and takes its name from this process.
# The image, credentials and database name are the ones the `server` job in
# .github/workflows/server.yml uses; change them together.

set -e -E -o pipefail

SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="derbynet-pytest-postgres-$$"
trap 'docker rm -f -v "$NAME" >/dev/null 2>&1 || true' EXIT

# Say which step failed: a busy machine once gave up waiting for Postgres
# with no message and an empty pytest log (#52 retro).
trap 'echo "pytest-postgres.sh: line $LINENO: \`$BASH_COMMAND\` failed" >&2' ERR

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=derbynet \
       -p 127.0.0.1::5432 postgres:17 >/dev/null
PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
# Under CPU load (tools/pytest-under-load.sh) Postgres can take over 30 s.
WAIT="${DERBYNET_PG_WAIT:-120}"
for _ in $(seq 1 "$WAIT"); do
  docker exec "$NAME" pg_isready -U postgres -q && break
  sleep 1
done
if ! docker exec "$NAME" pg_isready -U postgres -q; then
  echo "pytest-postgres.sh: Postgres in $NAME was not ready after $WAIT s" \
       "(set DERBYNET_PG_WAIT); its last log lines:" >&2
  docker logs --tail 20 "$NAME" >&2 || true
  exit 1
fi
trap - ERR

cd "$SERVER_DIR"
DATABASE_URL="postgres://postgres:postgres@127.0.0.1:$PORT/derbynet" uv run pytest "$@"
