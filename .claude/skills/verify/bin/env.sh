# Sourced by the verify helpers.  One verification instance per RUN.
# VERIFY_SERVER picks the server: php (default, a Docker image built from this
# checkout) or django (server/ run with this checkout's venv, no Docker).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
RUN="${VERIFY_RUN:-v1}"
PORT="${VERIFY_PORT:-8090}"
KIND="${VERIFY_SERVER:-php}"
case "$KIND" in php|django) ;; *) echo "VERIFY_SERVER must be php or django" >&2; exit 2;; esac
export DERBYNET_TEST_PREFIX="verify-$RUN"
export DERBYNET_TEST_PORT="$PORT"
BASE="http://127.0.0.1:$PORT"
SERVER="$DERBYNET_TEST_PREFIX-server"
EVIDENCE="$REPO/.claude/verify-evidence/$RUN"
JAR="$EVIDENCE/cookies.txt"
# The django server's database, settings and pid; cleanup removes them.
DJANGO_STATE="$REPO/server/var/verify-$RUN"
PIDFILE="$DJANGO_STATE/runserver.pid"
mkdir -p "$EVIDENCE"
