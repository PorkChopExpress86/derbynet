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
#   upload     with MAX_UPLOAD_SIZE=16M a 15 MiB photo.upload succeeds and a
#              17 MiB one answers 413; at the default (100M) the photo
#              stand's 30 MB photo succeeds
#   bind-mount a bind mount owned by another uid is given to derbynet, and
#              one it can't be given to stops the container with a message
#   client-ip  Django sees the client's address, not the unix socket's, and
#              not an X-Real-IP the client sent
#   forwarded-proto
#              a client's X-Forwarded-Proto counts only from
#              DERBYNET_TRUSTED_PROXIES
#   stop       docker stop ends the container at once, with status 0
#   reload     GUNICORN_RELOAD=1 shows a template edit without a rebuild
#   timeout    DERBYNET_REQUEST_TIMEOUT reaches gunicorn and nginx
#   engine     a volume refuses to start on the other database engine, in
#              both directions
#   postgres-password
#              compose's postgres profile with a password holding @ : / # %
#   bad-env    values that would reach nginx's configuration malformed stop
#              the container with a message
#   allowed-hosts
#              with DERBYNET_ALLOWED_HOSTS naming a host, the image's own
#              HEALTHCHECK passes and Docker reports the container healthy;
#              the named host answers and another is refused (#61)
#   upgrade    a volume made by OLD_IMAGE, holding racers and results, starts
#              under IMAGE, migrates, and keeps the racer and result counts
#   docs       on a network with no internet, /docs/quick-reference/ and a
#              guide's image answer from the guides the build rendered (#61),
#              /quickref.pdf answers 301 to it (D94), and under
#              DERBYNET_URL_PREFIX the page's links carry the prefix
#   backup     server/docker/README.md's backup and restore, with compose:
#              an event with a racer photo, a car photo and a video, backed
#              up, restored into fresh volumes, and all there.  PROFILE=postgres
#              runs it under the postgres profile.
#
# IMAGE names the image (default derbynet_server:dev).  DERBYNET_TEST_PREFIX
# (default derbynet-checks) prefixes every container and volume, and each
# check removes its own.  Each check prints what it saw, and the script exits
# non-zero at the first that fails.

set -e -E -u -o pipefail

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
    docker network ls -q --filter "name=^$PREFIX-" | xargs -r docker network rm >/dev/null
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

# The first of its arguments, such as a glob's first match.  (`ls | head -1`
# fails under pipefail when ls is still writing as head exits.)
first() { echo "$1"; }

# A JPEG of exactly $2 bytes: a real photo with random bytes after its end,
# which every decoder ignores.
padded_jpeg() {
    local out="$1" size="$2" photo
    photo="$(first "$TESTING"/data/headshots/*.jpg)"
    cp "$photo" "$out"
    head -c $((size - $(stat -c %s "$photo"))) /dev/urandom >> "$out"
}

check_upload() {
    start up1 "$PREFIX-upload" -e DERBYNET_ENABLE_TEST_ENDPOINTS=1 -e MAX_UPLOAD_SIZE=16M \
        -p 127.0.0.1::80
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
    stop up1

    say "at the default MAX_UPLOAD_SIZE, the photo stand's 30000000-byte photo"
    start up2 "$PREFIX-upload" -p 127.0.0.1::80
    url="http://$(docker port "$PREFIX-up2" 80/tcp)"
    rm -f "$jar"
    login "$url" "$jar" Photo
    padded_jpeg "$work/30mb.jpg" 30000000
    reply="$(curl -s -c "$jar" -b "$jar" -w '\nHTTP %{http_code}' -F action=photo.upload \
        -F repo=head -F MAX_FILE_SIZE=30000000 -F photo=@"$work/30mb.jpg" "$url/action.php")"
    echo "$reply" | tail -12
    echo "$reply" | grep -q 'HTTP 200' || fail "the 30 MB upload did not answer 200"
    echo "$reply" | grep -q '"code": "success"' || fail "the 30 MB upload did not succeed"
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
    say "the same with a forged X-Real-IP: 10.9.8.7"
    target="$(curl -s -o /dev/null -H 'X-Real-IP: 10.9.8.7' -H 'X-Real-Port: 4242' \
        -w '%{redirect_url}' "$url/kiosk.php")"
    echo "$target"
    [[ "$target" =~ address=${gateway//./\\.}%2B([0-9]+)$ ]] && [ "${BASH_REMATCH[1]}" != 4242 ] \
        || fail "a client's X-Real-IP or X-Real-Port reached Django"
}

# action.php refuses a POST whose Origin is not this server's own
# `<scheme>://<host>` with `notauthorized` (S34, legacy_api/views.py
# _foreign_origin), so an https Origin over plain HTTP passes that refusal
# only when Django takes the request for HTTPS.  (Past it, a server with no
# event answers 302 to setup.php.)
origin_post() { # origin_post <url> [curl options]: the refusal code, or "passed"
    local url="$1" body
    shift
    body="$(curl -s "$@" -H "Origin: https://${url#http://}" -d 'action=role.login&name=' \
        "$url/action.php")"
    if echo "$body" | grep -q notauthorized; then echo notauthorized; else echo passed; fi
}

check_forwarded_proto() {
    start fp1 "$PREFIX-proto" -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-fp1" 80/tcp)" code gateway
    gateway="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' "$PREFIX-fp1")"
    say "plain HTTP, Origin https://..., X-Forwarded-Proto: https, from an untrusted client"
    code="$(origin_post "$url" -H 'X-Forwarded-Proto: https')"
    echo "$code"
    [ "$code" = notauthorized ] || fail "Django took a client's X-Forwarded-Proto"
    stop fp1
    say "the same from DERBYNET_TRUSTED_PROXIES=$gateway"
    start fp2 "$PREFIX-proto" -p 127.0.0.1::80 -e DERBYNET_TRUSTED_PROXIES="$gateway"
    url="http://$(docker port "$PREFIX-fp2" 80/tcp)"
    code="$(origin_post "$url" -H 'X-Forwarded-Proto: https')"
    echo "$code"
    [ "$code" = passed ] || fail "a trusted proxy's X-Forwarded-Proto was not taken"
    say "and from it with no header, the connection's scheme"
    code="$(origin_post "$url")"
    echo "$code"
    [ "$code" = notauthorized ] || fail "Django took plain HTTP for HTTPS"
}

# The bare `wait` this guards against spun on about half the stops (2 of 4
# on one machine), so the check stops the container STOPS times.
check_stop() {
    local began took status lines round
    for round in $(seq "${STOPS:-8}"); do
        start st1 "$PREFIX-stop"
        began="$(date +%s%N)"
        docker stop "$PREFIX-st1" >/dev/null
        took=$(( ($(date +%s%N) - began) / 1000000 ))
        status="$(docker inspect -f '{{.State.ExitCode}}' "$PREFIX-st1")"
        lines="$(docker logs "$PREFIX-st1" 2>&1 | wc -l)"
        say "docker stop $round: $took ms, exit status $status, $lines log lines"
        [ "$status" = 0 ] || fail "the container exited with $status"
        # docker stop's grace is 10 s; the check allows half of it.
        (( took < 5000 )) || fail "docker stop took $took ms"
        (( lines < 200 )) || fail "the log grew to $lines lines while stopping"
        stop st1
    done
}

check_reload() {
    local work="$RUNNER_TEMP_DIR/reload" template marker
    rm -rf "$work" && mkdir -p "$work/server"
    tar -C "$ROOT/server" --exclude=./.venv --exclude=./var -cf - . | tar -C "$work/server" -xf -
    template="$work/server/accounts/templates/accounts/login.html"
    # One worker, which has the page cached before the edit: Django caches
    # templates even with DEBUG on, so only a restart shows the edit.
    start rl1 "$PREFIX-reload" -p 127.0.0.1::80 -e GUNICORN_RELOAD=1 -e GUNICORN_WORKERS=1 \
        -v "$work/server:/opt/derbynet/server:ro"
    local url="http://$(docker port "$PREFIX-rl1" 80/tcp)"
    marker="reload-check-$RANDOM"
    # The page is read whole before it is searched: grep -q's early exit
    # would fail curl with SIGPIPE under pipefail.
    local page
    page="$(curl -s "$url/login/?all")"
    [[ "$page" != *"$marker"* ]] || fail "the marker is there before the edit"
    sed -i "/{% block main %}/a <!-- $marker -->" "$template"
    say "after a template edit, with no rebuild"
    for _ in $(seq 30); do
        page="$(curl -s "$url/login/?all")"
        [[ "$page" == *"$marker"* ]] && break
        sleep 1
    done
    [[ "$page" == *"$marker"* ]] || fail "the edit never showed"
    echo "the login page shows <!-- $marker -->"
}

check_bad_env() {
    local setting out status
    for setting in 'MAX_UPLOAD_SIZE=16MB' 'DERBYNET_CONFIG_DIR=/var/lib/derbynet;evil' \
                   'DERBYNET_DATA_DIR=relative' 'DERBYNET_TRUSTED_PROXIES=10.0.0.1; evil' \
                   'DERBYNET_URL_PREFIX=/a b' 'DERBYNET_REQUEST_TIMEOUT=30s'; do
        status=0
        out="$(timeout 60 docker run --rm -e "$setting" "$IMAGE" 2>&1)" || status=$?
        say "$setting: exit status $status: $(echo "$out" | tail -1)"
        [ "$status" != 0 ] || fail "the container started with $setting"
        [[ "$out" == *"derbynet: ${setting%%=*}"* ]] || fail "no message names ${setting%%=*}"
    done
}

check_postgres_password() {
    local project="$PREFIX-pgpass" password='p@ss:w/rd#%'
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml"
                   --profile postgres)
    say "compose --profile postgres with POSTGRES_PASSWORD='$password'"
    POSTGRES_PASSWORD="$password" DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 \
        DERBYNET_HTTPS_PORT=127.0.0.1:0 "${compose[@]}" up -d --wait
    local log engine
    log="$("${compose[@]}" logs server 2>&1)"
    echo "$log" | grep 'derbynet:'
    [[ "$log" == *"using Postgres at postgres"* ]] || fail "the server did not use Postgres"
    engine="$("${compose[@]}" exec -T server cat /var/lib/derbynet/database-engine)"
    echo "database-engine: $engine"
    [ "$engine" = postgres ] || fail "the volume is not marked Postgres"
    "${compose[@]}" exec -T server sh -c '! ls /var/lib/derbynet/*.sqlite3 2>/dev/null' \
        || fail "a SQLite database was made"
    "${compose[@]}" down --volumes
}

check_timeout() {
    start t1 "$PREFIX-timeout" -e DERBYNET_REQUEST_TIMEOUT=600
    say "DERBYNET_REQUEST_TIMEOUT=600"
    docker exec "$PREFIX-t1" grep -h 'proxy_read_timeout' /etc/nginx/derbynet-proxy.conf
    docker exec "$PREFIX-t1" grep -q 'proxy_read_timeout 600s;' /etc/nginx/derbynet-proxy.conf \
        || fail "nginx's proxy_read_timeout is not 600s"
    local args
    args="$(docker exec "$PREFIX-t1" ps -e -o args)"
    args="$(grep '[g]unicorn' <<<"$args")"
    echo "${args%%$'\n'*}"
    [[ "$args" == *"--timeout 600 "* ]] || fail "gunicorn's --timeout is not 600"
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

    say "a SQLite volume refuses a Postgres DATABASE_URL"
    start e2 "$PREFIX-engine2"
    stop e2
    status=0
    out="$(timeout 120 docker run --name "$PREFIX-e3" -v "$PREFIX-engine2:/var/lib/derbynet" \
        -e DATABASE_URL=postgres://derbynet:x@nowhere.invalid:5432/derbynet "$IMAGE" 2>&1)" || status=$?
    echo "$out" | tail -2
    [ "$status" != 0 ] || fail "the server started on Postgres"
    echo "$out" | grep -q "race data is in sqlite" || fail "no message says why"
    docker rm -f "$PREFIX-e3" >/dev/null
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
        -F photo=@"$(first "$TESTING"/data/headshots/*.jpg)" "$url/action.php" | grep -q success \
        || fail "no racer photo"
    curl -s -b "$jar" -F action=photo.upload -F repo=car -F racerid="$racer" \
        -F photo=@"$(first "$TESTING"/data/carphotos/Car-*.jpg)" "$url/action.php" | grep -q success \
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
    (
        cd "$work"
        umask 077
        docker run --rm -v "$data:/data:ro" --entrypoint tar "$IMAGE" -C /data -czf - . > derbynet-data.tgz
        if [ "$profile" = postgres ]; then
            docker run --rm -v "$postgres:/data:ro" --entrypoint tar "$IMAGE" \
                -C /data -czf - . > derbynet-postgres.tgz
        fi
    )
    ls -l "$work"/*.tgz
    for file in "$work"/*.tgz; do
        [ "$(stat -c '%a %u' "$file")" = "600 $(id -u)" ] || fail "$file is not private to $(id -un)"
    done
    "${compose[@]}" start

    say "restore into fresh volumes and a fresh container"
    "${compose[@]}" down --volumes
    "${compose[@]}" create
    docker run --rm -i -v "$data:/data" --entrypoint tar "$IMAGE" -C /data -xzf - < "$work/derbynet-data.tgz"
    if [ "$profile" = postgres ]; then
        docker run --rm -i -v "$postgres:/data" --entrypoint tar "$IMAGE" \
            -C /data -xzf - < "$work/derbynet-postgres.tgz"
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

# fetch <container> <path>: the status, the content type and the body, from
# inside the container.
fetch() {
    docker exec "$PREFIX-$1" curl -sS -o /tmp/fetched -w '%{http_code} %{content_type} %{redirect_url}\n' \
        "http://127.0.0.1$2"
}

check_allowed_hosts() {
    local name="$PREFIX-hosts" probe code status
    say "DERBYNET_ALLOWED_HOSTS=derby.example.org: the image's HEALTHCHECK"
    start hosts "$PREFIX-hosts" -e DERBYNET_ALLOWED_HOSTS=derby.example.org
    probe="$(docker inspect -f '{{index .Config.Healthcheck.Test 1}}' "$name")"
    echo "$probe"
    status=0
    docker exec "$name" sh -c "$probe" || status=$?
    echo "exit status $status"
    [ "$status" = 0 ] || fail "the HEALTHCHECK fails with DERBYNET_ALLOWED_HOSTS set"
    for _ in $(seq 120); do
        [ "$(docker inspect -f '{{.State.Health.Status}}' "$name")" = healthy ] && break
        sleep 1
    done
    echo "docker: $(docker inspect -f '{{.State.Health.Status}}' "$name")"
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$name")" = healthy ] \
        || fail "the container is not healthy with DERBYNET_ALLOWED_HOSTS set"
    say "the named host answers, and another host is refused"
    code="$(docker exec "$name" curl -s -o /dev/null -w '%{http_code}' -H 'Host: derby.example.org' http://127.0.0.1/index.php)"
    echo "derby.example.org: $code"
    case "$code" in 2??|3??) ;; *) fail "the named host answered $code" ;; esac
    code="$(docker exec "$name" curl -s -o /dev/null -w '%{http_code}' -H 'Host: other.example.org' http://127.0.0.1/index.php)"
    echo "other.example.org: $code"
    [ "$code" = 400 ] || fail "another host answered $code"
    stop hosts
}

check_docs() {
    docker network create --internal "$PREFIX-offline" >/dev/null
    start docs "$PREFIX-docs" --network "$PREFIX-offline"
    say "the container reaches no other host"
    if docker exec "$PREFIX-docs" curl -sS -m 5 -o /dev/null https://example.org 2>&1; then
        fail "the container reached https://example.org"
    fi
    say "the quick reference, offline"
    local got
    got="$(fetch docs /docs/quick-reference/)"
    echo "$got"
    case "$got" in "200 text/html"*) ;; *) fail "/docs/quick-reference/ answered $got" ;; esac
    docker exec "$PREFIX-docs" grep -q '<h1 id="quick-reference">Quick reference</h1>' /tmp/fetched \
        || fail "/docs/quick-reference/ has no Quick reference heading"
    docker exec "$PREFIX-docs" grep -q 'href="/docs/quick-reference/"' /tmp/fetched \
        || fail "the help link does not open /docs/quick-reference/"
    say "a guide's image"
    got="$(fetch docs /docs/img/live-streaming/track-view-scene.jpg)"
    echo "$got"
    case "$got" in "200 image/jpeg"*) ;; *) fail "the image answered $got" ;; esac
    say "the old help link"
    got="$(fetch docs /quickref.pdf)"
    echo "$got"
    case "$got" in "301 "*"/docs/quick-reference/") ;; *) fail "/quickref.pdf answered $got" ;; esac
    stop docs
    say "under DERBYNET_URL_PREFIX=/derbynet"
    start docs-prefix "$PREFIX-docs" --network "$PREFIX-offline" -e DERBYNET_URL_PREFIX=/derbynet
    got="$(fetch docs-prefix /derbynet/docs/quick-reference/)"
    echo "$got"
    case "$got" in "200 text/html"*) ;; *) fail "/derbynet/docs/quick-reference/ answered $got" ;; esac
    docker exec "$PREFIX-docs-prefix" grep -q 'href="/derbynet/docs/quick-reference/"' /tmp/fetched \
        || fail "the help link has no /derbynet prefix"
    docker exec "$PREFIX-docs-prefix" grep -q 'href="../roles-and-passwords/"' /tmp/fetched \
        || fail "the guide's links are not relative"
}

RUNNER_TEMP_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/$PREFIX.XXXXXX")"
trap 'cleanup; rm -rf "$RUNNER_TEMP_DIR"' EXIT
[ $# -gt 0 ] || set -- tls secret user upload bind-mount client-ip forwarded-proto stop \
    reload timeout engine postgres-password bad-env allowed-hosts docs
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
        forwarded-proto) check_forwarded_proto ;;
        stop) check_stop ;;
        reload) check_reload ;;
        postgres-password) check_postgres_password ;;
        bad-env) check_bad_env ;;
        allowed-hosts) check_allowed_hosts ;;
        upgrade) check_upgrade ;;
        backup) check_backup ;;
        docs) check_docs ;;
        *) fail "no check named $check" ;;
    esac
    cleanup
    echo "ok: $check"
done
