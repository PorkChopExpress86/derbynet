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
# The https origin bin/phone serves this run at; start_django trusts it.
ORIGIN_FILE="$DJANGO_STATE/phone-origin"
# tailscale serve needs root: VERIFY_SUDO names the command that grants it.
SUDO="${VERIFY_SUDO:-sudo}"
mkdir -p "$EVIDENCE"

# Starts runserver on this run's database, migrated, and waits for doctor.
# DEBUG is off, as on race day, so templates are cached and --noreload keeps
# the code it started with: bin/restart picks up an edit.  runserver serves no
# static files without --insecure.  The python process itself is the pid, so
# cleanup stops the server and not a wrapper.
start_django() {
  export DERBYNET_DATA_DIR="$DJANGO_STATE/data" DERBYNET_CONFIG_DIR="$DJANGO_STATE/config"
  # Django refuses a form a phone posts from an origin it does not trust.
  if [ -f "$ORIGIN_FILE" ]; then
    export DERBYNET_CSRF_TRUSTED_ORIGINS="$(cat "$ORIGIN_FILE")"
  fi
  cd "$REPO/server"
  .venv/bin/python manage.py migrate -v 0 >> "$EVIDENCE/launch.log" 2>&1 \
    || { tail -20 "$EVIDENCE/launch.log" >&2; exit 1; }
  nohup .venv/bin/python manage.py runserver "127.0.0.1:$PORT" --noreload --insecure \
    >> "$EVIDENCE/server.log" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 30); do
    curl -s -o /dev/null "$BASE/login.php" && break
    sleep 1
  done
  "$REPO/.claude/skills/verify/bin/doctor"
}
