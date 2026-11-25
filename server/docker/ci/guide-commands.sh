#! /bin/bash
#
# Runs the commands the guides in docs/ tell the user to run, as the guides
# write them (#414).  Each stage below is named by rows of
# server/docs/guide-commands.tsv: the script asks tools/guide_commands.py for
# the commands registered to a stage and runs each one with `eval`, in this
# shell, in guide order, so a `cd`, an `umask` or a background job reaches the
# next command as it does for a reader.  The script holds no copy of a guide
# line; tests/test_guide_commands.py holds each registered stage to a function
# of this file and to a live workflow step that runs it.
#
# Usage: server/docker/ci/guide-commands.sh STAGE ...
#   .github/workflows/image.yml and server.yml name the stages
#   they run; GUIDE_WORK (default a new folder) keeps the state the stages of
#   one workflow job share, so the stages run in the order the workflow lists.
#
# Stages:
#   compose-env      docs/install-docker.md's Linux commands that write .env
#   compose-start    docker compose up -d: the server and Caddy answer on HTTP
#                    and HTTPS
#   plain-http       the one docker run command: a healthy server on port 80
#   time-zone        the TZ change, with docker run: the container has the new TZ
#   postgres         docker compose --profile postgres up -d (install and
#                    database guides): Postgres healthy, the server answers
#   proxies          the Nginx Proxy Manager, Traefik and nginx docker run
#                    commands, each in turn: a healthy container, the port
#                    it publishes
#   backup-compose   the Compose backup: two private archives, one holding a
#                    file put on the data volume, and the server running again
#   restore          the Compose restore, from those archives: the file is
#                    back and the server answers
#   backup-plain     the docker run backup, the same way
#   upgrade          the docker run upgrade: the new container keeps the
#                    volume's file
#   https-upgrade    the move from a docker run install to Compose, and the
#                    new certificate authority: the data volume's file is in
#                    Compose's volume, tls/ is gone, HTTPS answers
#   import-plain     docs/database.md's import into the docker run container:
#                    the import makes one more event
#   import-compose   its import through Compose, the same way
#   dev-setup        docs/developer-guide.md's Set up commands, from server/
#                    (uv sync, migrate)
#   dev-browser      its browser install, from server/: Playwright's Chromium
#                    is there
#   dev-shell-suite  its shell-suite block from the repository root: a
#                    development server in the background, and
#                    testing/reset-database.sh against it
#
# What differs from a reader's computer, and why the commands stay as written:
# - The guides name `derbynet`, `derbynet_data` and port 80, which no prefix
#   can change without editing them (D115).  The script refuses to start when
#   the Docker daemon holds a container, volume or network named derbynet*, a
#   `traefik` network, a `guide-` container, or ghcr.io's server image tagged
#   latest, so a daemon with data on it is never touched: run it on a CI runner
#   or in a throwaway docker:dind container, never on a daemon you use.
# - The environment: IMAGE (default derbynet_server:dev) is tagged as the
#   ghcr.io server image the guides name, so `docker run` finds it without
#   a pull; DERBYNET_IMAGE names the same image in a registry that the script
#   starts (guide-registry, on 127.0.0.1:5000), so `docker compose pull` pulls.
#   The dev stages set DERBYNET_DATA_DIR to a folder of the script's.
# - docs/database.md copies the reader's own folder, /path/to/pack42; the
#   import stages copy one they make from transfer/tests/fixtures/legacy,
#   then run the import as written.
# - GUIDE_HOST (default localhost) is where the stages reach a port that a
#   guide command publishes.
# - Not in a guide's text: the stages' own checks of what the commands did.
#
# WAIT_SECONDS (default 300) bounds each wait.

set -euo pipefail
trap 'exit 143' TERM INT

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SERVER_DIR="$ROOT/server"
IMAGE="${IMAGE:-derbynet_server:dev}"
GUIDE_HOST="${GUIDE_HOST:-localhost}"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
GHCR_SERVER=ghcr.io/porkchopexpress86/derbynet_server
REGISTRY_IMAGE=registry:3@sha256:82f6ff4a7f982960c8aa51617fea3aabc485df6e55cce80d22deb1de36f1bc3c
REG=""
GUARDED=""
WORK="${GUIDE_WORK:-$(mktemp -d)}"
mkdir -p "$WORK"

fail() { echo "FAILED: $*" >&2; diagnostics; exit 1; }
say() { echo "== $*"; }

diagnostics() {
    [ -n "$GUARDED" ] || return 0
    docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' >&2 || true
    docker ps -aq --filter 'name=^derbynet' --filter 'name=^guide-' \
        | while read -r container; do
            echo "-- $container" >&2
            docker logs --tail 30 "$container" >&2 2>&1 < /dev/null || true
        done
}

# What on the daemon the guides' commands name, one per line.
taken() {
    {
        docker ps -a --format '{{.Names}}'
        docker volume ls --format '{{.Name}}'
        docker network ls --format '{{.Name}}'
    } | grep -E '^(derbynet|guide-)|^traefik$' || true
    if docker image inspect "$GHCR_SERVER:latest" >/dev/null 2>&1; then
        echo "$GHCR_SERVER:latest"
    fi
}

guard() {
    local found
    docker info >/dev/null || { echo "guide-commands.sh: no Docker daemon answers" >&2; exit 2; }
    found="$(taken)"
    [ -z "$found" ] || {
        echo "guide-commands.sh: this Docker daemon already holds:" >&2
        echo "$found" >&2
        echo "The guides' commands name derbynet, derbynet_data, a traefik network" \
             "and port 80, and would change them. Run this on a CI runner or in a" \
             "throwaway docker:dind container, not on a daemon with data on it (D115)." >&2
        exit 2
    }
    GUARDED=1
}

# The containers, volumes and networks the guides' commands made.
reset_guide_state() {
    docker ps -aq --filter 'name=^derbynet' | xargs -r docker rm -fv >/dev/null
    docker network ls -q --filter 'name=^derbynet' | xargs -r docker network rm >/dev/null
    docker volume ls -q --filter 'name=^derbynet' | xargs -r docker volume rm >/dev/null
    docker network rm traefik >/dev/null 2>&1 || true
}

cleanup() {
    local status=$?
    trap - EXIT
    [ -n "${GUIDE_WORK:-}" ] || rm -rf "$WORK"
    [ -n "$GUARDED" ] || exit "$status"
    for pid in $(jobs -p); do kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true; done
    reset_guide_state || true
    docker ps -aq --filter 'name=^guide-' | xargs -r docker rm -fv >/dev/null || true
    docker volume ls -q --filter 'name=^guide-' | xargs -r docker volume rm >/dev/null || true
    docker network ls -q --filter 'name=^guide-' | xargs -r docker network rm >/dev/null || true
    docker rmi "$GHCR_SERVER:latest" ${REG:+"$REG/derbynet_server:ci"} >/dev/null 2>&1 || true
    exit "$status"
}
trap cleanup EXIT

wait_for() { # wait_for <what> <command ...>
    local what="$1" i
    shift
    for ((i = 0; i < WAIT_SECONDS; i++)); do
        "$@" >/dev/null 2>&1 && return 0
        sleep 1
    done
    fail "$what was not ready in $WAIT_SECONDS s"
}

http_ok() { curl -fsS -m 10 -o /dev/null "$@"; }
healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$1")" = healthy ]; }

# Starts the registry that DERBYNET_IMAGE comes from, once.
registry() {
    [ -z "$REG" ] || return 0
    docker run -d --name guide-registry -p 127.0.0.1:5000:5000 "$REGISTRY_IMAGE" >/dev/null
    REG=localhost:5000
    wait_for "the registry" registry_up
}

# Read from the registry's own log, since a daemon that is not this machine's
# publishes its port on its own host.
registry_up() { grep -q 'listening on' <<<"$(docker logs guide-registry 2>&1)"; }

# The server image as the guides name it, and in the registry.
server_image() {
    registry
    docker tag "$IMAGE" "$GHCR_SERVER:latest"
    docker tag "$IMAGE" "$REG/derbynet_server:ci"
    docker push -q "$REG/derbynet_server:ci" >/dev/null
    export DERBYNET_IMAGE="$REG/derbynet_server:ci"
}

# stage_records <stage>: RECORDS holds where, command, where, command ... of
# each command registered to the stage, in guide order.
RECORDS=()
stage_records() {
    (cd "$SERVER_DIR" && uv run --locked python -m tools.guide_commands stage "$1") \
        > "$WORK/stage-$1" || fail "no commands for stage $1"
    mapfile -d '' -t RECORDS < "$WORK/stage-$1"
}

# run_guide <stage> [hook]: each command registered to the stage, as written,
# in this shell; hook <where> <command> runs after each.
run_guide() {
    local hook="${2:-}" records where command i
    stage_records "$1"
    records=("${RECORDS[@]}")
    for ((i = 0; i < ${#records[@]}; i += 2)); do
        where="${records[i]}"
        command="${records[i + 1]}"
        say "docs/$where: $command"
        eval "$command" || fail "docs/$where exited $?: $command"
        [ -z "$hook" ] || "$hook" "$where" "$command"
    done
}

# The folder the install guide works in: compose.yaml saved in it, as the
# guide says (it is server/docker/compose.yaml).
install_folder() { # install_folder <name>
    local folder="$WORK/$1"
    mkdir -p "$folder"
    cp "$ROOT/server/docker/compose.yaml" "$folder/"
    cd "$folder"
}

put_file() { # put_file <container or compose service>: a file on the data volume
    if [ "$1" = server ]; then
        docker compose exec -T server sh -c 'echo guide-marker > /var/lib/derbynet/guide-marker'
    else
        docker exec "$1" sh -c 'echo guide-marker > /var/lib/derbynet/guide-marker'
    fi
}

has_file() { # has_file <container or compose service>
    local seen
    if [ "$1" = server ]; then
        seen="$(docker compose exec -T server cat /var/lib/derbynet/guide-marker)"
    else
        seen="$(docker exec "$1" cat /var/lib/derbynet/guide-marker)"
    fi
    [ "$seen" = guide-marker ] || fail "the data volume lost its file: $seen"
}

archive_private() {
    tar -tzf "$1" >/dev/null || fail "$1 is not a tar archive"
    [ "$(stat -c %a "$1")" = 600 ] || fail "$1 is not private (mode $(stat -c %a "$1"), umask 077)"
}

archive_has_file() {
    archive_private "$1"
    grep -q 'guide-marker$' <<<"$(tar -tzf "$1")" || fail "$1 does not hold the data volume's file"
}

serves() { # serves <url> [curl options]
    wait_for "$1" http_ok "$@"
}

stage_compose_env() {
    reset_guide_state
    server_image
    install_folder install
    rm -f .env
    run_guide compose-env
    cat .env
    [ "$(wc -l < .env)" = 4 ] || fail ".env has not the guide's four lines"
    grep -qx "DERBYNET_UID=$(id -u)" .env || fail ".env has not this user's uid"
    grep -qx "DERBYNET_ADDRESS=$(hostname -I | cut -d' ' -f1)" .env || fail ".env has not this computer's address"
}

stage_compose_start() {
    [ -f "$WORK/install/.env" ] || fail "compose-start needs the .env that stage compose-env writes"
    cd "$WORK/install"
    reset_guide_state
    server_image
    run_guide compose-start
    serves "http://$GUIDE_HOST/index.php"
    serves "https://$GUIDE_HOST/index.php" -k
    [ "$(docker compose ps --status running --format '{{.Service}}' | sort | tr '\n' ' ')" = 'caddy server ' ] \
        || fail "Compose does not run exactly the server and Caddy"
}

stage_plain_http() {
    reset_guide_state
    server_image
    run_guide plain-http
    wait_for "the server's health check" healthy derbynet
    serves "http://$GUIDE_HOST/index.php"
}

stage_time_zone() {
    stage_plain_http
    run_guide time-zone
    [ "$(docker exec derbynet printenv TZ)" = America/Denver ] || fail "the container's TZ is not America/Denver"
    wait_for "the server's health check" healthy derbynet
    serves "http://$GUIDE_HOST/index.php"
}

stage_postgres() {
    cd "$WORK/install"
    reset_guide_state
    server_image
    run_guide postgres
    wait_for "Postgres" healthy derbynet-postgres-1
    serves "http://$GUIDE_HOST/index.php"
    [ "$(docker compose --profile postgres ps --status running --format '{{.Service}}' | sort | tr '\n' ' ')" = 'caddy postgres server ' ] \
        || fail "Compose does not run the server, Caddy and Postgres"
}

proxy_after() { # proxy_after <where> <command>
    local published
    wait_for "the server's health check" healthy derbynet
    published="$(docker port derbynet 80/tcp 2>/dev/null || true)"
    case "$2" in
        *"--network traefik"*) [ -z "$published" ] || fail "the Traefik container publishes $published" ;;
        *"-p 127.0.0.1:8000:80"*) [ "$published" = 127.0.0.1:8000 ] || fail "nginx's container publishes '$published'" ;;
        *"-p 8000:80"*) [[ "$published" == *:8000* ]] || fail "Nginx Proxy Manager's container publishes '$published'" ;;
        *) fail "a proxy command this stage does not know: $2" ;;
    esac
    docker rm -f derbynet >/dev/null
    docker volume rm derbynet >/dev/null
}

stage_proxies() {
    reset_guide_state
    server_image
    docker network create traefik >/dev/null
    run_guide proxies proxy_after
}

stage_backup_compose() {
    cd "$WORK/install"
    stage_compose_start
    put_file server
    run_guide backup-compose
    archive_has_file derbynet-data.tgz
    archive_private derbynet-caddy.tgz
    serves "http://$GUIDE_HOST/index.php"
}

stage_restore() {
    cd "$WORK/install"
    [ -f derbynet-data.tgz ] || fail "restore needs the archives that stage backup-compose makes"
    server_image
    run_guide restore
    serves "http://$GUIDE_HOST/index.php"
    has_file server
}

stage_backup_plain() {
    install_folder install
    stage_plain_http
    put_file derbynet
    run_guide backup-plain
    archive_has_file derbynet-data.tgz
    serves "http://$GUIDE_HOST/index.php"
}

stage_upgrade() {
    stage_plain_http
    put_file derbynet
    run_guide upgrade
    wait_for "the server's health check" healthy derbynet
    has_file derbynet
}

stage_https_upgrade() {
    stage_plain_http
    put_file derbynet
    docker exec derbynet sh -c 'mkdir -p /var/lib/derbynet/tls && echo old > /var/lib/derbynet/tls/old'
    install_folder https-upgrade
    run_guide compose-env
    run_guide https-upgrade
    serves "http://$GUIDE_HOST/index.php"
    serves "https://$GUIDE_HOST/index.php" -k
    has_file server
    docker compose exec -T server test ! -e /var/lib/derbynet/tls || fail "tls/ is still on the data volume"
    [ -z "$(docker ps -aq --filter 'name=^derbynet$')" ] || fail "the docker run container is still there"
}

# A folder like the reader's /path/to/pack42: an older DerbyNet's
# derbynet.sqlite3 with its photo folders.
legacy_folder() { # legacy_folder <dir>
    local fixtures="$SERVER_DIR/transfer/tests/fixtures/legacy"
    mkdir -p "$1"
    cp -r "$fixtures/v10-photos/." "$1/"
    python3 -I -c 'import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.executescript(open(sys.argv[2]).read()); c.commit()' \
        "$1/derbynet.sqlite3" "$fixtures/v10.sql"
    chmod -R a+rX "$1"
}

events() { # events <exec command ...>: the server's count of events
    "$@" python manage.py shell --no-imports -c \
        'from core.models import Event; print(Event.objects.count())'
}

stage_import_plain() {
    local before
    stage_plain_http
    legacy_folder "$WORK/pack42-plain"
    docker cp "$WORK/pack42-plain" derbynet:/tmp/pack42
    before="$(events docker exec -u derbynet derbynet)"
    run_guide import-plain
    [ "$(events docker exec -u derbynet derbynet)" = $((before + 1)) ] || fail "the import made no event"
}

stage_import_compose() {
    local before
    cd "$WORK/install"
    stage_compose_start
    legacy_folder "$WORK/pack42-compose"
    docker compose cp "$WORK/pack42-compose" server:/tmp/pack42
    before="$(events docker compose exec -T -u derbynet server)"
    run_guide import-compose
    [ "$(events docker compose exec -T -u derbynet server)" = $((before + 1)) ] || fail "the import made no event"
}

stage_dev_setup() {
    export DERBYNET_DATA_DIR="$WORK/dev-data"
    cd "$SERVER_DIR"
    run_guide dev-setup
    [ -f "$DERBYNET_DATA_DIR/derbynet.sqlite3" ] || fail "migrate made no database in $DERBYNET_DATA_DIR"
    git -C "$ROOT" diff --exit-code -- server/uv.lock || fail "uv sync changed uv.lock"
}

stage_dev_browser() {
    cd "$SERVER_DIR"
    run_guide dev-browser
    uv run --locked python -c 'import os, sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
    sys.exit(not os.access(p.chromium.executable_path, os.X_OK))' \
        || fail "Playwright has no Chromium after the install"
}

wait_for_server() { # wait_for_server <where> <command>: a command ending in & starts a server
    case "$2" in
        *"&") wait_for "the development server" http_ok http://127.0.0.1:8000/index.php ;;
    esac
}

stage_dev_shell_suite() {
    export DERBYNET_DATA_DIR="$WORK/dev-data"
    cd "$ROOT"
    set -m
    run_guide dev-shell-suite wait_for_server
    for pid in $(jobs -p); do kill -TERM -- "-$pid"; done
    set +m
}

[ $# -gt 0 ] || { sed -n '/^# Stages:/,/^# What differs/p' "$0" >&2; exit 2; }
# The development stages use no Docker, so a machine with none runs them.
for stage in "$@"; do
    case "$stage" in dev-*) ;; *) guard; break ;; esac
done

for stage in "$@"; do
    say "stage $stage"
    case "$stage" in
        compose-env) stage_compose_env ;;
        compose-start) stage_compose_start ;;
        plain-http) stage_plain_http ;;
        time-zone) stage_time_zone ;;
        postgres) stage_postgres ;;
        proxies) stage_proxies ;;
        backup-compose) stage_backup_compose ;;
        restore) stage_restore ;;
        backup-plain) stage_backup_plain ;;
        upgrade) stage_upgrade ;;
        https-upgrade) stage_https_upgrade ;;
        import-plain) stage_import_plain ;;
        import-compose) stage_import_compose ;;
        dev-setup) stage_dev_setup ;;
        dev-browser) stage_dev_browser ;;
        dev-shell-suite) stage_dev_shell_suite ;;
        *) echo "guide-commands.sh: no stage $stage" >&2; exit 2 ;;
    esac
    say "stage $stage passed"
done
