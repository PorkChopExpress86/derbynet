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

set -e -E -o pipefail

SERVER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="derbynet-pytest-postgres-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=derbynet \
       -p 127.0.0.1::5432 postgres:17 >/dev/null
PORT="$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')"
for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U postgres -q && break
  sleep 1
done
docker exec "$NAME" pg_isready -U postgres -q

cd "$SERVER_DIR"
DATABASE_URL="postgres://postgres:postgres@127.0.0.1:$PORT/derbynet" uv run pytest "$@"
