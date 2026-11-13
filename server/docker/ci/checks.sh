#! /bin/bash
#
# The image's acceptance checks (#58) that need a container but not the whole
# shell suite.  .github/workflows/image.yml runs each as a step; run them
# locally the same way, after building the image:
#
#   IMAGE=derbynet_server:dev server/docker/ci/checks.sh tls secret user ...
#
# Checks:
#   tls        curl -k https://localhost/index.php answers; the certificate
#              stays the same for a new container on the same volume and
#              differs on a fresh volume; the image holds no key (#64 S35)
#   secret     secret_key: mode 0600, kept across containers, new per volume,
#              and not written when DJANGO_SECRET_KEY is set
#   user       ps -o user,comm: gunicorn runs as derbynet, not root
#   upload     a 15 MiB photo.upload succeeds and a 17 MiB one answers 413,
#              under the default MAX_UPLOAD_SIZE=16M
#   bind-mount a bind mount owned by another uid is given to derbynet, and
#              one it can't be given to stops the container with a message
#   client-ip  Django sees the client's address, not the unix socket's
#   timeout    DERBYNET_REQUEST_TIMEOUT reaches gunicorn and nginx
#   engine     a volume that ran on Postgres refuses to start on SQLite
#   upgrade    a volume made by OLD_IMAGE, holding racers and results, starts
#              under IMAGE, migrates, and keeps the racer and result counts
#   backup     server/docker/README.md's backup and restore, with compose:
#              an event with a racer photo, a car photo and a video, backed
#              up, restored into fresh volumes, and all there.  PROFILE=postgres
#              runs it under the postgres profile.
#
# IMAGE names the image (default derbynet_server:dev).  DERBYNET_TEST_PREFIX
# (default derbynet-checks) prefixes every container and volume, and each
# check removes its own.  Each check prints what it saw, and the script exits
# non-zero at the first that fails.

set -e -E -o pipefail

IMAGE="${IMAGE:-derbynet_server:dev}"
PREFIX="${DERBYNET_TEST_PREFIX:-derbynet-checks}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TESTING="$ROOT/testing"

fail() { echo "FAILED: $*" >&2; exit 1; }
say() { echo "== $*"; }

cleanup() {
    # The bind-mount and backup checks leave files owned by other uids.
    [ -d "${RUNNER_TEMP_DIR:-}" ] && docker run --rm -v "$RUNNER_TEMP_DIR:/t" \
        --entrypoint sh "$IMAGE" -c 'rm -rf /t/bind /t/backup' >/dev/null 2>&1 || true
    docker ps -aq --filter "name=^$PREFIX-" | xargs -r docker rm -f >/dev/null
    docker volume ls -q --filter "name=^$PREFIX-" | xargs -r docker volume rm >/dev/null
}
trap cleanup EXIT
trap 'exit 143' TERM INT

# start <name> <volume or host dir> [docker run options...]: starts a
# container and waits until nginx and Django answer.
start() {
    local name="$PREFIX-$1" volume="$2"
    shift 2
    docker run -d --name "$name" -v "$volume:/var/lib/derbynet" "$@" "$IMAGE" >/dev/null
    wait_ready "$name"
}

wait_ready() {
    for _ in $(seq 120); do
        if docker exec "$1" curl -fsS -o /dev/null http://127.0.0.1/index.php 2>/dev/null; then
            return 0
        fi
        [ "$(docker inspect -f '{{.State.Running}}' "$1")" = true ] || break
        sleep 1
    done
    docker logs "$1" >&2
    fail "$1 did not answer on http://127.0.0.1/index.php"
}

stop() { docker rm -f "$PREFIX-$1" >/dev/null; }

served_fingerprint() {
    docker exec "$PREFIX-$1" sh -c \
        'openssl s_client -connect 127.0.0.1:443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256'
}

check_tls() {
    say "the image holds no TLS key or secret: /var/lib/derbynet in the image is"
    local baked
    baked="$(docker run --rm --entrypoint ls "$IMAGE" -A /var/lib/derbynet)"
    echo "[${baked}]"
    [ -z "$baked" ] || fail "the image ships files in /var/lib/derbynet: $baked"

    start tls1 "$PREFIX-tls-a"
    say "curl -k https://localhost/index.php"
    local code
    code="$(docker exec "$PREFIX-tls1" curl -sk -o /dev/null -w '%{http_code} %{redirect_url}' https://localhost/index.php)"
    echo "$code"
    case "$code" in 200*|301*) ;; *) fail "https://localhost/index.php answered $code" ;; esac
    code="$(docker exec "$PREFIX-tls1" curl -skL -o /dev/null -w '%{http_code}' https://localhost/index.php)"
    echo "after redirects: $code"
    [ "$code" = 200 ] || fail "https://localhost/index.php ends in $code"
    say "plain HTTP is served, with no redirect to HTTPS"
    code="$(docker exec "$PREFIX-tls1" curl -s -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/action.php?query=role.list')"
    echo "$code"
    case "$code" in *https:*) fail "plain HTTP redirects to HTTPS: $code" ;; esac

    say "openssl x509 -fingerprint, first container on volume A"
    local first file second other
    first="$(served_fingerprint tls1)"
    file="$(docker exec "$PREFIX-tls1" openssl x509 -noout -fingerprint -sha256 -in /var/lib/derbynet/tls/cert.pem)"
    echo "served:  $first"
    echo "on disk: $file"
    [ -n "$first" ] && [ "$first" = "$file" ] || fail "nginx serves another certificate than tls/cert.pem"
    docker exec "$PREFIX-tls1" ls -l /var/lib/derbynet/tls

    stop tls1
    start tls2 "$PREFIX-tls-a"
    second="$(served_fingerprint tls2)"
    say "after docker rm and a new container on volume A"
    echo "served:  $second"
    [ "$first" = "$second" ] || fail "the certificate changed with the container"

    start tls3 "$PREFIX-tls-b"
    other="$(served_fingerprint tls3)"
    say "a fresh volume B"
    echo "served:  $other"
    [ "$other" != "$first" ] || fail "two fresh volumes got the same certificate"

    say "the generated certificate lasts 825 days"
    docker exec "$PREFIX-tls3" openssl x509 -noout -startdate -enddate -in /var/lib/derbynet/tls/cert.pem
    docker exec "$PREFIX-tls3" sh -c 'openssl x509 -noout -checkend $((825 * 86400 - 3600)) -in /var/lib/derbynet/tls/cert.pem && ! openssl x509 -noout -checkend $((825 * 86400 + 3600)) -in /var/lib/derbynet/tls/cert.pem' \
        || fail "the certificate does not last 825 days"
    stop tls3

    say "an expired certificate the container made is made again"
    docker run --rm -v "$PREFIX-tls-b:/var/lib/derbynet" --entrypoint sh "$IMAGE" -c \
        'cd /var/lib/derbynet/tls && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
             -nodes -subj /CN=DerbyNet -not_before 20200101000000Z -not_after 20200201000000Z \
             -keyout key.pem -out cert.pem 2>/dev/null &&
         openssl x509 -noout -fingerprint -sha256 -in cert.pem > .generated'
    start tls5 "$PREFIX-tls-b"
    docker logs "$PREFIX-tls5" 2>&1 | grep 'expired' || fail "no new certificate was made"
    docker exec "$PREFIX-tls5" openssl x509 -noout -checkend 0 -in /var/lib/derbynet/tls/cert.pem \
        || fail "the certificate is still expired"
    stop tls5

    say "an expired certificate the user put there is left alone"
    docker run --rm -v "$PREFIX-tls-b:/var/lib/derbynet" --entrypoint sh "$IMAGE" -c \
        'cd /var/lib/derbynet/tls && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
             -nodes -subj /CN=theirs -not_before 20200101000000Z -not_after 20200201000000Z \
             -keyout key.pem -out cert.pem 2>/dev/null'
    start tls6 "$PREFIX-tls-b"
    docker exec "$PREFIX-tls6" openssl x509 -noout -subject -in /var/lib/derbynet/tls/cert.pem | grep theirs \
        || fail "the user's certificate was replaced"
    stop tls6

    say "a cert.pem and key.pem the user put there are used as they are"
    local mine
    mine="$(docker run --rm -v "$PREFIX-tls-c:/var/lib/derbynet" --entrypoint sh "$IMAGE" -c \
        'mkdir -p /var/lib/derbynet/tls && cd /var/lib/derbynet/tls &&
         openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=user-supplied \
             -keyout key.pem -out cert.pem 2>/dev/null &&
         openssl x509 -noout -fingerprint -sha256 -in cert.pem')"
    start tls4 "$PREFIX-tls-c"
    echo "theirs:  $mine"
    echo "served:  $(served_fingerprint tls4)"
    [ "$(served_fingerprint tls4)" = "$mine" ] || fail "the user's certificate was not served"
    stop tls4
}

check_secret() {
    start s1 "$PREFIX-secret-a"
    local mode first second other
    mode="$(docker exec "$PREFIX-s1" stat -c '%a %U' /var/lib/derbynet/secret_key)"
    say "secret_key mode and owner: $mode"
    [ "$mode" = "600 derbynet" ] || fail "secret_key is $mode"
    first="$(docker exec "$PREFIX-s1" sha256sum /var/lib/derbynet/secret_key)"
    stop s1
    start s2 "$PREFIX-secret-a"
    second="$(docker exec "$PREFIX-s2" sha256sum /var/lib/derbynet/secret_key)"
    [ "$first" = "$second" ] || fail "secret_key changed with the container"
    start s3 "$PREFIX-secret-b"
    other="$(docker exec "$PREFIX-s3" sha256sum /var/lib/derbynet/secret_key)"
    say "sha256 of secret_key: volume A twice, then volume B"
    printf '%s\n%s\n%s\n' "$first" "$second" "$other"
    [ "$first" != "$other" ] || fail "two fresh volumes got the same secret_key"
    start s4 "$PREFIX-secret-c" -e DJANGO_SECRET_KEY=from-the-environment
    if docker exec "$PREFIX-s4" test -e /var/lib/derbynet/secret_key; then
        fail "secret_key was written although DJANGO_SECRET_KEY is set"
    fi
    say "with DJANGO_SECRET_KEY set, no secret_key file"
}

check_user() {
    start u1 "$PREFIX-user"
    say "docker exec <container> ps -o user,comm"
    local ps
    ps="$(docker exec "$PREFIX-u1" ps -e -o user,comm)"
    echo "$ps"
    echo "$ps" | grep -q '^derbynet  *gunicorn' || fail "no gunicorn runs as derbynet"
    if echo "$ps" | grep -v '^derbynet' | grep -q gunicorn; then
        fail "a gunicorn process runs as another user"
    fi
}

# A JPEG of exactly $2 bytes: a real photo with random bytes after its end,
# which every decoder ignores.
padded_jpeg() {
    local out="$1" size="$2" photo
    photo="$(ls "$TESTING"/data/headshots/*.jpg | head -1)"
    cp "$photo" "$out"
    head -c $((size - $(stat -c %s "$photo"))) /dev/urandom >> "$out"
}

check_upload() {
    start up1 "$PREFIX-upload" -e DERBYNET_ENABLE_TEST_ENDPOINTS=1 -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-up1" 80/tcp)"
    local work="$RUNNER_TEMP_DIR/upload" jar="$RUNNER_TEMP_DIR/upload/cookies"
    suite_copy "$work"
    new_event "$url"
    (cd "$work" && bash testing/reset-database.sh "$url" >/dev/null)
    login "$url" "$jar" Photo
    padded_jpeg "$work/15m.jpg" $((15 * 1024 * 1024))
    padded_jpeg "$work/17m.jpg" $((17 * 1024 * 1024))
    local reply code
    say "photo.upload of a $(stat -c %s "$work/15m.jpg")-byte photo"
    reply="$(curl -s -c "$jar" -b "$jar" -w '\nHTTP %{http_code}' -F action=photo.upload \
        -F repo=head -F photo=@"$work/15m.jpg" "$url/action.php")"
    echo "$reply"
    echo "$reply" | grep -q 'HTTP 200' || fail "the 15 MiB upload did not answer 200"
    echo "$reply" | grep -q 'success' || fail "the 15 MiB upload did not succeed"
    say "photo.upload of a $(stat -c %s "$work/17m.jpg")-byte photo"
    code="$(curl -s -o /dev/null -c "$jar" -b "$jar" -w '%{http_code}' -F action=photo.upload \
        -F repo=head -F photo=@"$work/17m.jpg" "$url/action.php")"
    echo "HTTP $code"
    [ "$code" = 413 ] || fail "the 17 MiB upload answered $code, not 413"
}

check_bind_mount() {
    local dir="$RUNNER_TEMP_DIR/bind"
    say "a bind mount owned by uid 4321 is given to derbynet"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint sh "$IMAGE" -c \
        'rm -rf /t/bind && mkdir -p /t/bind/events && touch /t/bind/events/old && chown -R 4321:4321 /t/bind && chmod 755 /t/bind'
    start b1 "$dir"
    docker exec "$PREFIX-b1" ls -ln /var/lib/derbynet /var/lib/derbynet/events
    docker exec "$PREFIX-b1" sh -c '! find /var/lib/derbynet -path /var/lib/derbynet/tls -prune -o -uid 4321 -print | grep .' \
        || fail "files still belong to uid 4321"
    stop b1
    say "one it may not chown (no CAP_CHOWN) stops with a message"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint sh "$IMAGE" -c \
        'rm -rf /t/bind && mkdir -p /t/bind && chown 4321:4321 /t/bind && chmod 755 /t/bind'
    local status=0 out
    out="$(timeout 120 docker run --name "$PREFIX-b2" --cap-drop CHOWN -v "$dir:/var/lib/derbynet" "$IMAGE" 2>&1)" || status=$?
    echo "$out" | tail -3
    echo "exit status $status"
    [ "$status" != 0 ] || fail "the container started on a folder derbynet can't write"
    echo "$out" | grep -q 'not writable by the derbynet user' || fail "no message names the folder"
    if docker run --rm -v "$dir:/d" --entrypoint stat "$IMAGE" -c %a /d | grep -q 7$; then
        fail "the folder was opened to everyone"
    fi
    docker rm -f "$PREFIX-b2" >/dev/null
}

check_client_ip() {
    start ip1 "$PREFIX-ip" -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-ip1" 80/tcp)" gateway target
    # kiosk.php with no address answers 302 to ?address=<client address>+<port>,
    # from REMOTE_ADDR and REMOTE_PORT, as PHP's kiosk.php:8-10 does
    # (ui/kiosk_pages/views.py); with no port it would stand in 100 to 200.
    target="$(curl -s -o /dev/null -w '%{redirect_url}' "$url/kiosk.php")"
    gateway="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' "$PREFIX-ip1")"
    say "kiosk.php from the host, whose address in the container's network is $gateway"
    echo "$target"
    [[ "$target" =~ address=${gateway//./\\.}%2B([0-9]+)$ ]] \
        || fail "the kiosk's address is not the client's ($gateway)"
    (( BASH_REMATCH[1] > 200 )) || fail "the kiosk's port is the stand-in ${BASH_REMATCH[1]}"
}

check_timeout() {
    start t1 "$PREFIX-timeout" -e DERBYNET_REQUEST_TIMEOUT=600
    say "DERBYNET_REQUEST_TIMEOUT=600"
    docker exec "$PREFIX-t1" grep -h 'proxy_read_timeout' /etc/nginx/derbynet-proxy.conf
    docker exec "$PREFIX-t1" grep -q 'proxy_read_timeout 600s;' /etc/nginx/derbynet-proxy.conf \
        || fail "nginx's proxy_read_timeout is not 600s"
    docker exec "$PREFIX-t1" ps -e -o args | grep '[g]unicorn' | head -1
    docker exec "$PREFIX-t1" ps -e -o args | grep '[g]unicorn' | grep -q -- '--timeout 600' \
        || fail "gunicorn's --timeout is not 600"
}

check_engine() {
    say "a volume marked as Postgres refuses to start on SQLite"
    docker run --rm -v "$PREFIX-engine:/var/lib/derbynet" --entrypoint sh "$IMAGE" -c \
        'echo postgres > /var/lib/derbynet/database-engine'
    local status=0 out
    out="$(timeout 120 docker run --name "$PREFIX-e1" -v "$PREFIX-engine:/var/lib/derbynet" "$IMAGE" 2>&1)" || status=$?
    echo "$out" | tail -2
    [ "$status" != 0 ] || fail "the server started on SQLite"
    echo "$out" | grep -q "race data is in Postgres" || fail "no message says why"
    docker rm -f "$PREFIX-e1" >/dev/null
}

# A copy of testing/ in $1, whose scripts write beside themselves.
suite_copy() {
    rm -rf "$1" && mkdir -p "$1" && cp -r "$TESTING" "$1/testing"
}

# An event on a server with none, as exercise-one-database.sh makes one.
new_event() {
    curl -sf -d 'action=setup.nodata&ez-new=checks' "$1/action.php" | grep -q success \
        || fail "setup.nodata did not make an event at $1"
}

login() { # login <url> <jar> <role>
    local password
    password="$(sed -n -e "s/^$3://p" "$TESTING/default.passwords")"
    curl -sf -c "$2" -b "$2" -d "action=role.login&name=$3&password=$password" \
        "$1/action.php" | grep -q success || fail "$3 could not log in"
}

# Racers, and results from one race of every round, in a new event.
race_some_heats() { # race_some_heats <url> <work dir>
    suite_copy "$2"
    new_event "$1"
    (cd "$2" && bash testing/reset-database.sh "$1" && bash testing/import-roster.sh "$1" \
        && bash testing/run-scout-heats.sh "$1") >"$2/populate.log" 2>&1 \
        || { tail -20 "$2/populate.log"; fail "the event could not be set up"; }
}

counts() { # counts <container>: racers, and lanes with a finish time
    docker exec -u derbynet "$1" python manage.py shell --no-imports -c '
from racing.models import HeatLane
from roster.models import Racer
print("racers", Racer.objects.count(), "results", HeatLane.objects.exclude(finish_time=None).count())'
}

check_upgrade() {
    [ -n "${OLD_IMAGE:-}" ] || fail "OLD_IMAGE names the previous image"
    say "a volume made by $OLD_IMAGE"
    IMAGE="$OLD_IMAGE" start old1 "$PREFIX-upgrade" -e DERBYNET_ENABLE_TEST_ENDPOINTS=1 -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-old1" 80/tcp)"
    race_some_heats "$url" "$RUNNER_TEMP_DIR/upgrade"
    local before after
    before="$(counts "$PREFIX-old1")"
    docker exec -u derbynet "$PREFIX-old1" python manage.py showmigrations --plan | tail -3
    stop old1
    say "the same volume under $IMAGE"
    start new1 "$PREFIX-upgrade"
    docker logs "$PREFIX-new1" 2>&1 | grep -E 'Applying|No migrations to apply' || true
    if docker exec -u derbynet "$PREFIX-new1" python manage.py showmigrations --plan | grep -q '^\[ \]'; then
        fail "migrations are left unapplied"
    fi
    after="$(counts "$PREFIX-new1")"
    echo "before: $before"
    echo "after:  $after"
    [ "$before" = "$after" ] || fail "the counts changed"
    case "$before" in "racers 0 "*|*"results 0") fail "the old volume held no racers or no results" ;; esac
}

# The backup and restore of server/docker/README.md, step for step.
check_backup() {
    local profile="${PROFILE:-}" project="$PREFIX-backup" work="$RUNNER_TEMP_DIR/backup"
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml"
                   -f "$ROOT/server/docker/ci/compose.ci.yaml" ${profile:+--profile "$profile"})
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    suite_copy "$work"
    "${compose[@]}" up -d --wait
    local url="http://$("${compose[@]}" port server 80)"
    new_event "$url"
    (cd "$work" && bash testing/reset-database.sh "$url" && bash testing/import-roster.sh "$url") \
        >"$work/populate.log" 2>&1 || { tail "$work/populate.log"; fail "no event"; }
    local jar="$work/cookies" racer
    login "$url" "$jar" RaceCoordinator
    racer="$(curl -s -b "$jar" "$url/action.php?query=racer.list" | jq -r '.racers[0].racerid')"
    curl -s -b "$jar" -F action=photo.upload -F repo=head -F racerid="$racer" \
        -F photo=@"$(ls "$TESTING"/data/headshots/*.jpg | head -1)" "$url/action.php" | grep -q success \
        || fail "no racer photo"
    curl -s -b "$jar" -F action=photo.upload -F repo=car -F racerid="$racer" \
        -F photo=@"$(ls "$TESTING"/data/carphotos/Car-*.jpg | head -1)" "$url/action.php" | grep -q success \
        || fail "no car photo"
    head -c 300000 /dev/urandom > "$work/replay.mkv"
    curl -s -b "$jar" -F action=video.upload -F video=@"$work/replay.mkv;filename=Heat-1.mkv" \
        "$url/action.php" | grep -q success || fail "no video"
    local before after
    before="$(backup_state "$url" "$jar" "$racer")"
    echo "$before"

    say "backup: stop the server, copy the volumes, start it again"
    local data postgres
    data="$("${compose[@]}" volumes -q | grep '_data$')"
    postgres="$("${compose[@]}" volumes -q | grep '_postgres$' || true)"
    "${compose[@]}" stop
    docker run --rm -v "$data:/data:ro" -v "$work:/backup" --entrypoint tar "$IMAGE" \
        -C /data -czf /backup/derbynet-data.tgz .
    if [ "$profile" = postgres ]; then
        docker run --rm -v "$postgres:/data:ro" -v "$work:/backup" --entrypoint tar "$IMAGE" \
            -C /data -czf /backup/derbynet-postgres.tgz .
    fi
    ls -l "$work"/*.tgz

    say "restore into fresh volumes and a fresh container"
    "${compose[@]}" down --volumes
    "${compose[@]}" create
    docker run --rm -v "$data:/data" -v "$work:/backup" --entrypoint tar "$IMAGE" \
        -C /data -xzf /backup/derbynet-data.tgz
    if [ "$profile" = postgres ]; then
        docker run --rm -v "$postgres:/data" -v "$work:/backup" --entrypoint tar "$IMAGE" \
            -C /data -xzf /backup/derbynet-postgres.tgz
    fi
    "${compose[@]}" up -d --wait
    url="http://$("${compose[@]}" port server 80)"
    rm -f "$jar"
    login "$url" "$jar" RaceCoordinator
    after="$(backup_state "$url" "$jar" "$racer")"
    echo "$after"
    [ "$before" = "$after" ] || fail "the restore differs from the backup"
    "${compose[@]}" down --volumes
}

# The event's name and racers, each photo as served, and the video file.
backup_state() { # backup_state <url> <jar> <racerid>
    local head car
    echo "racers $(curl -s -b "$2" "$1/action.php?query=racer.list" | jq '.racers | length')," \
         "sha256 of their ids and names $(curl -s -b "$2" "$1/action.php?query=racer.list" \
           | jq -c '[.racers[] | [.racerid, .firstname, .lastname, .carnumber]]' | sha256sum | cut -c1-16)"
    head="$(curl -s -b "$2" "$1/action.php?query=racer.list" | jq -r ".racers[] | select(.racerid == $3) | .headshot")"
    car="$(curl -s -b "$2" "$1/action.php?query=racer.list" | jq -r ".racers[] | select(.racerid == $3) | .car_photo")"
    [ -n "$head" ] && [ "$head" != null ] || fail "racer $3 has no racer photo"
    [ -n "$car" ] && [ "$car" != null ] || fail "racer $3 has no car photo"
    echo "racer photo $(curl -s -b "$2" "$1/$head" | sha256sum | cut -c1-16)"
    echo "car photo $(curl -s -b "$2" "$1/$car" | sha256sum | cut -c1-16)"
    echo "video $("${compose[@]}" exec -T server sh -c 'find /var/lib/derbynet -name Heat-1.mkv -exec sha256sum {} +' | cut -c1-16)"
}

RUNNER_TEMP_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/$PREFIX.XXXXXX")"
trap 'cleanup; rm -rf "$RUNNER_TEMP_DIR"' EXIT
[ $# -gt 0 ] || set -- tls secret user upload bind-mount client-ip timeout engine
for check in "$@"; do
    case "$check" in
        tls) check_tls ;;
        secret) check_secret ;;
        user) check_user ;;
        upload) check_upload ;;
        bind-mount) check_bind_mount ;;
        client-ip) check_client_ip ;;
        timeout) check_timeout ;;
        engine) check_engine ;;
        upgrade) check_upgrade ;;
        backup) check_backup ;;
        *) fail "no check named $check" ;;
    esac
    cleanup
    echo "ok: $check"
done
