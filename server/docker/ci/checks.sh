#! /bin/bash
#
# The image's acceptance checks (#58) that need a container but not the whole
# shell suite.  .github/workflows/image.yml runs each as a step; run them
# locally the same way, after building the image:
#
#   IMAGE=derbynet_server:dev server/docker/ci/checks.sh tls secret user ...
#
# Checks:
#   tls        the image serves plain HTTP alone (D107): it exposes port 80
#              only, nothing listens on 443, a new volume gets no tls/, and
#              the image holds no key
#   secret     secret_key: mode 0600, kept across containers, new per volume,
#              and not written when DJANGO_SECRET_KEY is set
#   user       ps -o user,comm: gunicorn runs as derbynet, not root
#   upload     with MAX_UPLOAD_SIZE=16M a 15 MiB photo.upload succeeds and a
#              17 MiB one answers 413; at the default (100M) the photo
#              stand's 30 MB photo succeeds
#   bind-mount with no DERBYNET_UID, derbynet takes a bind mount's non-root
#              owner (D106); with the host user's DERBYNET_UID and
#              DERBYNET_GID, a photo the server writes is the host user's
#              (ls -ln); one it can't be given to stops the container with a
#              message
#   uid-env    DERBYNET_UID and DERBYNET_GID on a named volume: the server,
#              an exec -u derbynet and the volume's files take them, www-data
#              still serves a photo, and ids other accounts and groups hold
#              and the range's ends work (D106)
#   uid-root   uid or gid 0, a malformed id, and one without the other (D108)
#              stop the container with one line (D106)
#   uid-takeover
#              a volume of the image's uid goes to a new DERBYNET_UID at the
#              next start, with one log line; a restart, with derbynet's
#              home left away as a killed start leaves it, and a new container
#              with the same uid log none and change no file, and the
#              restart puts the home back (D106)
#   uid-exec   with the .env lines docs/install-docker.md writes, docker
#              compose exec -u derbynet runs manage.py check as the host user
#   client-ip  Django sees the client's address, not the unix socket's, and
#              not an X-Real-IP the client sent; from DERBYNET_TRUSTED_PROXIES
#              it sees the address X-Forwarded-For names and the port
#              X-Real-Port names, and from anyone else neither (D118)
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
#   exec-database
#              docker compose exec -u derbynet ... python manage.py uses the
#              server's database: under the postgres profile database.md's
#              import lands in Postgres and no SQLite file appears; with
#              Postgres stopped it fails with one message naming the host;
#              with no profile, it reads the server's SQLite (#308)
#   root       with DERBYNET_UID=2345 (D106): manage.py check, run with
#              docker compose exec and no -u, fails with one line naming the
#              command and -u derbynet; a refused manage.py shell writes
#              nothing; collectstatic, one of the build's commands, still runs
#              as root; and manage.py check passes with -u derbynet, as uid
#              2345 (D103)
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
#   timer      /timer.php as RaceCoordinator, at / and /derbynet/: every
#              script, image and stylesheet it links, and the worker and
#              images its scripts load, answer 200 with their type (#312)
#   backup     server/docker/README.md's backup and restore, with compose:
#              an event with a racer photo, a car photo and a video, backed
#              up, restored into fresh volumes, and all there, with Caddy's
#              certificate authority the same (D120).  PROFILE=postgres
#              runs it under the postgres profile.
#   caddy      compose.yaml on this computer's network address with
#              DERBYNET_ADDRESS: the server publishes no port; HTTPS is
#              trusted through Caddy's root, served at /derbynet-ca.crt, for
#              the address with no name sent and for another name
#              (on_demand); one on_demand warning; plain HTTP with no
#              redirect; X-Forwarded-Proto and the client's address through
#              Caddy, and a client's forged headers ignored; tls/ with
#              cert.pem alone logs one line and uses Caddy's authority; a
#              malformed DERBYNET_ADDRESS stops Caddy (D116-D119)
#   old-cert   a volume the v0.1.0-rc2 image made keeps its certificate
#              under compose.yaml's Caddy, with and without a name sent, and
#              its tls/ becomes the host user's; an expired pair put there
#              is served as it is (D107, D119)
#   readme-compose
#              the top-level README's Compose example and commands, run as
#              written, serve HTTPS trusted through Caddy's root (D107);
#              docker compose is 2.23.1 or later
#   acme       with DERBYNET_DOMAIN and DERBYNET_ACME_CA, Caddy gets a
#              certificate for the domain from Pebble, over a pair in tls/
#              (D119, D121)
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

# The image of the test release v0.1.0-rc2, the last published before D107,
# whose entrypoint made tls/cert.pem and key.pem on a new volume and whose
# nginx served them on 443.  Pinned by digest, so the old volume is one an
# install of that release made, whatever the PR's base holds.
OLD_TLS_IMAGE="${OLD_TLS_IMAGE:-ghcr.io/porkchopexpress86/derbynet_server:v0.1.0-rc2@sha256:f94d9dd6260addcb46b496ff2528f939f8ffd57fcd0aaa81ea18038084b9bb8b}"

# old_tls_volume <volume>: starts OLD_TLS_IMAGE on a fresh volume, prints the
# SHA-256 fingerprint its nginx served on 443, and removes the container.
# Fails unless that is the fingerprint of the volume's tls/cert.pem.
old_tls_volume() {
    local name="$PREFIX-oldtls" served file
    docker run -d --name "$name" -v "$1:/var/lib/derbynet" "$OLD_TLS_IMAGE" >/dev/null
    wait_ready "$name"
    served="$(docker exec "$name" sh -c \
        'openssl s_client -connect 127.0.0.1:443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256')"
    file="$(docker exec "$name" openssl x509 -noout -fingerprint -sha256 -in /var/lib/derbynet/tls/cert.pem)"
    echo "$OLD_TLS_IMAGE served: $served" >&2
    echo "its tls/cert.pem:      $file" >&2
    [ -n "$served" ] && [ "$served" = "$file" ] || fail "$OLD_TLS_IMAGE did not serve its tls/cert.pem"
    docker rm -f "$name" >/dev/null
    echo "$served"
}

# compose_version_at_least <major.minor.patch>: docker compose is that
# version or later.  compose.yaml's Caddyfile is a `configs: content:`, which
# Compose has taken since 2.23.1 ("Introduced in Docker Compose version
# 2.23.1", https://docs.docker.com/reference/compose-file/configs/).
compose_version_at_least() {
    local have
    have="$(docker compose version --short)"
    have="${have#v}"
    echo "docker compose $have" >&2
    local lowest
    lowest="$(sort -V <<<"$1"$'\n'"${have%%[+-]*}")"
    [ "${lowest%%$'\n'*}" = "$1" ]
}

# The image serves plain HTTP alone (D107): nothing of TLS in it or on a new
# volume, and no listener on 443.
check_tls() {
    say "the image holds no TLS key or secret: /var/lib/derbynet in the image is"
    local baked exposed code listening
    baked="$(docker run --rm --entrypoint ls "$IMAGE" -A /var/lib/derbynet)"
    echo "[${baked}]"
    [ -z "$baked" ] || fail "the image ships files in /var/lib/derbynet: $baked"
    exposed="$(docker image inspect -f '{{range $p, $_ := .Config.ExposedPorts}}{{$p}} {{end}}' "$IMAGE")"
    echo "EXPOSE: $exposed"
    [ "$exposed" = "80/tcp " ] || fail "the image exposes $exposed, not 80/tcp alone"

    start tls1 "$PREFIX-tls-a"
    say "plain HTTP is served, with no redirect to HTTPS"
    code="$(docker exec "$PREFIX-tls1" curl -s -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/action.php?query=role.list')"
    echo "$code"
    # With no event yet, Django answers 302 to setup.php over the same scheme.
    case "$code" in *https:*) fail "plain HTTP redirects to HTTPS: $code" ;; [23]??*) ;; *) fail "plain HTTP answered $code" ;; esac
    say "the listening sockets, from /proc/net/tcp and tcp6 (port 80 is 0050, 443 is 01BB)"
    listening="$(docker exec "$PREFIX-tls1" sh -c 'cat /proc/net/tcp /proc/net/tcp6' \
        | awk '$4 == "0A" {split($2, a, ":"); print a[2]}' | sort -u | xargs)"
    echo "$listening"
    [[ " $listening " == *" 0050 "* ]] || fail "nothing listens on port 80"
    [[ " $listening " != *" 01BB "* ]] || fail "something listens on port 443"
    say "a new volume gets no tls/"
    docker exec "$PREFIX-tls1" ls -A /var/lib/derbynet
    if docker exec "$PREFIX-tls1" test -e /var/lib/derbynet/tls; then
        fail "the server made tls/ on a new volume"
    fi
    stop tls1
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
    if grep -q gunicorn <<<"$(grep -v '^derbynet' <<<"$ps")"; then
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

# The ids derbynet keeps when nothing gives it others (D106): the image's own,
# as its /etc/passwd holds them before the entrypoint runs.
image_ids() { docker run --rm --entrypoint sh "$IMAGE" -c 'echo "$(id -u derbynet) $(id -g derbynet)"'; }

# ids <container>: "<uid> <gid>" a `docker exec -u derbynet` runs as, then
# each gunicorn's, from /proc, since ps prints uid 4294967294 as -2.
ids() {
    echo "exec -u derbynet: $(docker exec -u derbynet "$PREFIX-$1" sh -c 'echo "$(id -u) $(id -g)"')"
    echo "gunicorn: $(docker exec "$PREFIX-$1" sh -c \
        'for pid in $(pgrep -x gunicorn); do echo $(awk "/^[UG]id:/ {print \$2}" /proc/$pid/status); done' | sort -u | xargs)"
}

# owned_by <container> <uid> <gid>: lists the data folder, and fails on a file
# there with another owner.
owned_by() {
    docker exec "$PREFIX-$1" ls -lnA /var/lib/derbynet
    docker exec "$PREFIX-$1" sh -c "! find /var/lib/derbynet \
        \( ! -uid $2 -o ! -gid $3 \) -print | grep ." || fail "files under /var/lib/derbynet belong to another uid or gid than $2:$3"
}

# An event with the roster, and racer 1's photo uploaded: prints the racer
# photo's URL path.  photo_racer <url> <work dir>
photo_racer() {
    local jar="$2/cookies" racer head
    suite_copy "$2"
    new_event "$1"
    (cd "$2" && bash testing/reset-database.sh "$1" && bash testing/import-roster.sh "$1") \
        >"$2/populate.log" 2>&1 || { tail "$2/populate.log" >&2; fail "no event"; }
    login "$1" "$jar" RaceCoordinator
    racer="$(curl -s -b "$jar" "$1/action.php?query=racer.list" | jq -r '.racers[0].racerid')"
    grep -q success <<<"$(curl -s -b "$jar" -F action=photo.upload -F repo=head -F racerid="$racer" \
        -F photo=@"$(first "$TESTING"/data/headshots/*.jpg)" "$1/action.php")" || fail "no racer photo"
    head="$(curl -s -b "$jar" "$1/action.php?query=racer.list" | jq -r ".racers[] | select(.racerid == $racer) | .headshot")"
    [ -n "$head" ] && [ "$head" != null ] || fail "racer $racer has no photo"
    echo "$head"
}

# served <url> <path> <jar>: the photo, through nginx's www-data and the
# gunicorn socket, answers 200 as an image.
served() {
    local got
    got="$(curl -s -b "$3" -o /dev/null -w '%{http_code} %{content_type}' "$1/$2")"
    echo "GET /$2: $got"
    [[ "$got" == "200 image/"* ]] || fail "the photo answered $got"
}

check_bind_mount() {
    local dir="$RUNNER_TEMP_DIR/bind" default status out
    default="$(image_ids)"
    say "a bind mount owned by uid 4321, gid 4321, with no DERBYNET_UID: derbynet takes that owner (D106)"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint sh "$IMAGE" -c \
        'rm -rf /t/bind && mkdir -p /t/bind/events && touch /t/bind/events/old && chown -R 4321:4321 /t/bind && chmod 755 /t/bind'
    start b1 "$dir"
    out="$(ids b1)"
    echo "$out"
    [ "$out" = $'exec -u derbynet: 4321 4321\ngunicorn: 4321 4321' ] || fail "derbynet did not take uid 4321, gid 4321"
    owned_by b1 4321 4321
    stop b1

    say "a bind mount owned by uid 4321 in root's group keeps the image's gid, ${default#* } (D109)"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint sh "$IMAGE" -c \
        'rm -rf /t/bind && mkdir -p /t/bind && chown 4321:0 /t/bind && chmod 755 /t/bind'
    start b2 "$dir"
    out="$(ids b2)"
    echo "$out"
    [ "$out" = "exec -u derbynet: 4321 ${default#* }"$'\n'"gunicorn: 4321 ${default#* }" ] \
        || fail "derbynet did not take uid 4321 with the image's gid"
    stop b2

    say "the host user's folder, with DERBYNET_UID=\$(id -u) and DERBYNET_GID=\$(id -g): a photo the server writes is the host user's"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint rm "$IMAGE" -rf /t/bind
    mkdir "$dir"
    start b3 "$dir" -e DERBYNET_UID="$(id -u)" -e DERBYNET_GID="$(id -g)" \
        -e DERBYNET_ENABLE_TEST_ENDPOINTS=1 -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-b3" 80/tcp)" head photo
    head="$(photo_racer "$url" "$RUNNER_TEMP_DIR/bind-work")"
    served "$url" "$head" "$RUNNER_TEMP_DIR/bind-work/cookies"
    photo="$(sed -n 1p <<<"$(find "$dir" -name '*.jpg' -type f -print | sort)")"
    [ -n "$photo" ] || fail "no photo on the bind mount"
    say "ls -ln, on the host, of the photo the server wrote; id -u and id -g are $(id -u) $(id -g)"
    (cd "$dir" && ls -ln "${photo#"$dir"/}")
    [ "$(stat -c '%u %g' "$photo")" = "$(id -u) $(id -g)" ] || fail "the photo is not the host user's"
    if find "$dir" \( ! -uid "$(id -u)" -o ! -gid "$(id -g)" \) -print | grep .; then
        fail "files on the bind mount are not the host user's"
    fi
    stop b3

    say "one it may not chown (no CAP_CHOWN), with DERBYNET_UID=4322, stops with a message"
    docker run --rm -v "$RUNNER_TEMP_DIR:/t" --entrypoint sh "$IMAGE" -c \
        'rm -rf /t/bind && mkdir -p /t/bind && chown 4321:4321 /t/bind && chmod 755 /t/bind'
    status=0
    out="$(timeout 120 docker run --name "$PREFIX-b4" --cap-drop CHOWN -e DERBYNET_UID=4322 \
        -e DERBYNET_GID=4322 -v "$dir:/var/lib/derbynet" "$IMAGE" 2>&1)" || status=$?
    echo "$out" | tail -3
    echo "exit status $status"
    [ "$status" != 0 ] || fail "the container started on a folder derbynet can't write"
    echo "$out" | grep -q "not writable by the derbynet user (uid 4322). On a bind mount, run 'sudo chown -R 4322:4322 <folder>'" \
        || fail "no message names the folder and the chown"
    if grep -q 7$ <<<"$(docker run --rm -v "$dir:/d" --entrypoint stat "$IMAGE" -c %a /d)"; then
        fail "the folder was opened to everyone"
    fi
    docker rm -f "$PREFIX-b4" >/dev/null
}

# DERBYNET_UID and DERBYNET_GID on a named volume (D106): a changed uid and
# gid, ones other accounts and groups hold, and the ends of the range.
check_uid_env() {
    local out
    say "DERBYNET_UID=2345 DERBYNET_GID=2346 on a named volume"
    start ue1 "$PREFIX-uid-env-a" -e DERBYNET_UID=2345 -e DERBYNET_GID=2346 \
        -e DERBYNET_ENABLE_TEST_ENDPOINTS=1 -p 127.0.0.1::80
    out="$(ids ue1)"
    echo "$out"
    [ "$out" = $'exec -u derbynet: 2345 2346\ngunicorn: 2345 2346' ] || fail "derbynet does not run as 2345:2346"
    local url="http://$(docker port "$PREFIX-ue1" 80/tcp)" head
    say "www-data, nginx's user, is in derbynet's group at its new gid, and serves a photo"
    docker exec "$PREFIX-ue1" sh -c 'getent group derbynet; id www-data'
    [ "$(docker exec "$PREFIX-ue1" getent group derbynet)" = "derbynet:x:2346:www-data" ] \
        || fail "www-data left derbynet's group"
    head="$(photo_racer "$url" "$RUNNER_TEMP_DIR/uid-env")"
    served "$url" "$head" "$RUNNER_TEMP_DIR/uid-env/cookies"
    owned_by ue1 2345 2346
    say "no file outside the volume belongs to derbynet's old uid or gid, which no account holds now"
    out="$(docker exec "$PREFIX-ue1" find / -xdev \( -nouser -o -nogroup \) -print)"
    echo "[${out}]"
    [ -z "$out" ] || fail "files in the image belong to no account"
    stop ue1

    local uid gid name
    for pair in 33:33 1:100 1000:1000 4294967294:4294967294; do
        uid="${pair%:*}" gid="${pair#*:}" name="ue-${uid}"
        say "DERBYNET_UID=$uid DERBYNET_GID=$gid: $(docker run --rm --entrypoint bash "$IMAGE" -c \
            'u="$(getent passwd "$1" | cut -d: -f1)"; g="$(getent group "$2" | cut -d: -f1)"
             echo "uid $1 is ${u:-no account}'"'"'s and gid $2 is ${g:-no group}'"'"'s in the image"' \
            bash "$uid" "$gid")"
        start "$name" "$PREFIX-uid-env-$uid" -e DERBYNET_UID="$uid" -e DERBYNET_GID="$gid"
        out="$(ids "$name")"
        echo "$out"
        [ "$out" = "exec -u derbynet: $uid $gid"$'\n'"gunicorn: $uid $gid" ] || fail "derbynet does not run as $uid:$gid"
        owned_by "$name" "$uid" "$gid"
        if [ "$uid" = 1000 ]; then
            # The base image's ubuntu account is gone (D111), so the usual
            # first host user is derbynet's alone.
            out="$(docker exec "$PREFIX-$name" getent passwd 1000 | cut -d: -f1 | xargs)"
            echo "getent passwd 1000: $out"
            [ "$out" = derbynet ] || fail "uid 1000 names $out, not derbynet alone"
        fi
        stop "$name"
    done
}

# uid or gid 0, malformed ids, and one without the other each stop the
# container with one line (D106).
check_uid_root() {
    local setting expected out status
    while IFS='|' read -r setting expected; do
        status=0
        # shellcheck disable=SC2086 # each setting is one or two -e options
        out="$(timeout 60 docker run --rm $setting "$IMAGE" 2>&1)" || status=$?
        say "$setting: exit status $status"
        echo "$out"
        [ "$status" != 0 ] || fail "the container started with $setting"
        [ "$out" = "derbynet: $expected" ] || fail "$setting did not stop with the one line: $expected"
    done <<'EOF_CASES'
-e DERBYNET_UID=0 -e DERBYNET_GID=1000|DERBYNET_UID=0 is root's id, and the image refuses to run the server as root. Set it to your own user's, as 'id -u' and 'id -g' print them.
-e DERBYNET_UID=1000 -e DERBYNET_GID=0|DERBYNET_GID=0 is root's id, and the image refuses to run the server as root. Set it to your own user's, as 'id -u' and 'id -g' print them.
-e DERBYNET_UID=00 -e DERBYNET_GID=00|DERBYNET_UID=00 is root's id, and the image refuses to run the server as root. Set it to your own user's, as 'id -u' and 'id -g' print them.
-e DERBYNET_UID=0.5 -e DERBYNET_GID=1000|DERBYNET_UID must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '0.5'
-e DERBYNET_UID=1.5 -e DERBYNET_GID=1000|DERBYNET_UID must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '1.5'
-e DERBYNET_UID=-1 -e DERBYNET_GID=1000|DERBYNET_UID must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '-1'
-e DERBYNET_UID=1000 -e DERBYNET_GID=4294967295|DERBYNET_GID must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '4294967295'
-e DERBYNET_UID=4294967294.5 -e DERBYNET_GID=1000|DERBYNET_UID must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '4294967294.5'
-e DERBYNET_UID=1000|DERBYNET_GID is unset; set both DERBYNET_UID and DERBYNET_GID, or neither.
-e DERBYNET_GID=1000|DERBYNET_UID is unset; set both DERBYNET_UID and DERBYNET_GID, or neither.
EOF_CASES
}

# A volume whose files belong to the image's uid, as after an upgrade, goes
# to a new DERBYNET_UID at the next start with one line (D106).  A restart,
# and a new container, with the same uid log no line and change no file.
check_uid_takeover() {
    local default line lines before after out
    default="$(image_ids)"
    say "a volume made with the image's ids ($default)"
    start ut1 "$PREFIX-uid-takeover"
    docker exec -u derbynet "$PREFIX-ut1" sh -c 'mkdir -p /var/lib/derbynet/kept && echo x > /var/lib/derbynet/kept/file'
    owned_by ut1 "${default% *}" "${default#* }"
    stop ut1
    say "the same volume with DERBYNET_UID=2345 DERBYNET_GID=2346"
    start ut2 "$PREFIX-uid-takeover" -e DERBYNET_UID=2345 -e DERBYNET_GID=2346
    line="derbynet: the files under /var/lib/derbynet belong to uid ${default% *} (gid ${default#* }); giving them to uid 2345 (gid 2346), once, which takes longer the more photos the folder holds"
    lines="$(docker logs "$PREFIX-ut2" 2>&1 | grep -F 'giving them to' || true)"
    echo "$lines"
    [ "$lines" = "$line" ] || fail "the start did not log the one line: $line"
    owned_by ut2 2345 2346
    before="$(docker exec "$PREFIX-ut2" stat -c '%n %z' /var/lib/derbynet/secret_key /var/lib/derbynet/kept /var/lib/derbynet/kept/file)"

    say "docker restart, with the same uid and derbynet's home left at /nonexistent, as by a start killed between its two usermods: no line, no file changed, and the home back"
    docker exec "$PREFIX-ut2" usermod -d /nonexistent derbynet
    docker restart "$PREFIX-ut2" >/dev/null
    wait_ready "$PREFIX-ut2"
    lines="$(docker logs "$PREFIX-ut2" 2>&1 | grep -cF 'giving them to' || true)"
    echo "takeover lines in the log of both starts: $lines"
    [ "$lines" = 1 ] || fail "the restart logged a takeover"
    out="$(docker exec "$PREFIX-ut2" getent passwd derbynet)"
    echo "$out"
    [ "$out" = "derbynet:x:2345:2346::/var/lib/derbynet:/usr/sbin/nologin" ] \
        || fail "derbynet's home stayed away from /var/lib/derbynet"
    after="$(docker exec "$PREFIX-ut2" stat -c '%n %z' /var/lib/derbynet/secret_key /var/lib/derbynet/kept /var/lib/derbynet/kept/file)"
    echo "$after"
    [ "$before" = "$after" ] || fail "the restart changed a file's ctime"
    stop ut2

    say "a new container on the volume, with the same uid: no line, and no file changed"
    start ut3 "$PREFIX-uid-takeover" -e DERBYNET_UID=2345 -e DERBYNET_GID=2346
    lines="$(docker logs "$PREFIX-ut3" 2>&1 | grep -cF 'giving them to' || true)"
    echo "takeover lines: $lines"
    [ "$lines" = 0 ] || fail "the new container logged a takeover"
    after="$(docker exec "$PREFIX-ut3" stat -c '%n %z' /var/lib/derbynet/secret_key /var/lib/derbynet/kept /var/lib/derbynet/kept/file)"
    [ "$before" = "$after" ] || fail "the new container changed a file's ctime"
    owned_by ut3 2345 2346
}

# guide_env_lines: the guide's command that writes DERBYNET_UID and
# DERBYNET_GID into .env (docs/install-docker.md, With Docker Compose).
guide_env_lines() {
    awk '/^```sh$/ {on=1; block=""; next}
         /^```$/ {if (on && block ~ /DERBYNET_UID/ && block ~ />> \.env/) printf "%s", block; on=0; next}
         on {block = block $0 "\n"}' "$1"
}

# The Compose steps of docs/install-docker.md with the guide's .env lines: a
# `docker compose exec -u derbynet` runs manage.py as the host user (D106).
check_uid_exec() {
    local project="$PREFIX-uidexec" work="$RUNNER_TEMP_DIR/uidexec" lines out status
    mkdir -p "$work"
    cp "$ROOT/server/docker/compose.yaml" "$work/"
    lines="$(guide_env_lines "$ROOT/docs/install-docker.md")"
    [ -n "$lines" ] || fail "docs/install-docker.md has no command writing DERBYNET_UID into .env"
    say "the guide's .env lines, run in the folder beside compose.yaml"
    echo "$lines"
    (cd "$work" && bash -euo pipefail -c "$lines")
    cat "$work/.env"
    local compose=(docker compose -p "$project" -f "$work/compose.yaml")
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    "${compose[@]}" up -d --wait
    say "docker compose exec -u derbynet server sh -c 'id -u; id -g; python manage.py check'"
    status=0
    out="$("${compose[@]}" exec -T -u derbynet server sh -c 'id -u; id -g; python manage.py check' 2>&1)" || status=$?
    echo "$out"
    echo "exit status $status"
    [ "$status" = 0 ] || fail "manage.py check failed as derbynet"
    [[ "$out" == "$(id -u)"$'\n'"$(id -g)"$'\n'"System check identified no issues"* ]] \
        || fail "manage.py check did not run as the host user, $(id -u):$(id -g)"
    "${compose[@]}" exec -T server sh -c "! find /var/lib/derbynet \
        \( ! -uid $(id -u) -o ! -gid $(id -g) \) -print | grep ." || fail "files on the volume are not the host user's"
    "${compose[@]}" down --volumes
}

# kiosk_address <url> <client address> [curl options]: kiosk.php with no
# address answers 302 to ?address=<client address>+<port>, from REMOTE_ADDR
# and REMOTE_PORT, as PHP's kiosk.php:8-10 does (ui/kiosk_pages/views.py);
# with no port it would stand in 100 to 200.  Fails unless the address is the
# client's and the port a real one; prints the port.
kiosk_address() {
    local url="$1" client="$2" target
    shift 2
    target="$(curl -s -o /dev/null -w '%{redirect_url}' "$@" "$url/kiosk.php")"
    echo "$target" >&2
    [[ "$target" =~ address=${client//./\\.}%2B([0-9]+)$ ]] \
        || fail "the kiosk's address is not the client's ($client)"
    (( BASH_REMATCH[1] > 200 )) || fail "the kiosk's port is the stand-in ${BASH_REMATCH[1]}"
    echo "${BASH_REMATCH[1]}"
}

check_client_ip() {
    start ip1 "$PREFIX-ip" -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-ip1" 80/tcp)" gateway port
    gateway="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' "$PREFIX-ip1")"
    say "kiosk.php from the host, whose address in the container's network is $gateway"
    kiosk_address "$url" "$gateway" >/dev/null
    say "the same with a forged X-Real-IP: 10.9.8.7"
    port="$(kiosk_address "$url" "$gateway" -H 'X-Real-IP: 10.9.8.7' -H 'X-Real-Port: 4242')"
    [ "$port" != 4242 ] || fail "a client's X-Real-Port reached Django"
    say "and with a forged X-Forwarded-For: 10.9.8.7"
    kiosk_address "$url" "$gateway" -H 'X-Forwarded-For: 10.9.8.7' >/dev/null
    stop ip1
    say "from DERBYNET_TRUSTED_PROXIES=$gateway: the address X-Forwarded-For names, and X-Real-Port (D118)"
    start ip2 "$PREFIX-ip" -p 127.0.0.1::80 -e DERBYNET_TRUSTED_PROXIES="$gateway"
    url="http://$(docker port "$PREFIX-ip2" 80/tcp)"
    port="$(kiosk_address "$url" 10.9.8.7 -H 'X-Forwarded-For: 10.9.8.7' -H 'X-Real-Port: 4242')"
    [ "$port" = 4242 ] || fail "the trusted proxy's X-Real-Port did not reach Django"
    say "the last address in X-Forwarded-For that is no trusted proxy's: of 10.9.8.6, 10.9.8.7, $gateway it is 10.9.8.7"
    kiosk_address "$url" 10.9.8.7 -H "X-Forwarded-For: 10.9.8.6, 10.9.8.7, $gateway" >/dev/null
    say "and with no X-Forwarded-For, the connection's address"
    kiosk_address "$url" "$gateway" >/dev/null
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

# lan_address: this computer's first address on its network, as the guide's
# Compose steps write it into .env (D116).
lan_address() { hostname -I | awk '{print $1}'; }

# served_cert <host:port> [openssl s_client options]: the SHA-256 fingerprint
# and the names of the certificate served there.
served_cert() {
    local at="$1"
    shift
    openssl s_client -connect "$at" "$@" </dev/null 2>/dev/null \
        | openssl x509 -noout -fingerprint -sha256 -ext subjectAltName
}

# compose.yaml's Caddy in front of the server (D107, D116-D118), on this
# computer's network address as a phone reaches it.
check_caddy() {
    local project="$PREFIX-caddy" work="$RUNNER_TEMP_DIR/caddy" lan http https code ports port out status
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml")
    lan="$(lan_address)"
    mkdir -p "$work"
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT="$lan:0" DERBYNET_HTTPS_PORT="$lan:0" DERBYNET_ADDRESS="$lan"
    say "compose.yaml with DERBYNET_ADDRESS=$lan"
    "${compose[@]}" up -d --wait
    http="$lan:$("${compose[@]}" port caddy 80 | sed 's/.*://')"
    https="$lan:$("${compose[@]}" port caddy 443 | sed 's/.*://')"

    say "the server publishes no port; Caddy publishes 80 and 443 (D117)"
    ports="$(docker inspect -f '{{json .NetworkSettings.Ports}}' "$project-server-1")"
    echo "server: $ports"
    [[ "$ports" != *HostPort* ]] || fail "the server publishes a port"

    say "Caddy's root, from http://$http/derbynet-ca.crt and over https"
    curl -fsS -o "$work/root.crt" "http://$http/derbynet-ca.crt"
    curl -fsSk -o "$work/root-https.crt" "https://$https/derbynet-ca.crt"
    openssl x509 -noout -subject -in "$work/root.crt"
    cmp "$work/root.crt" "$work/root-https.crt" || fail "http and https serve different roots"

    say "https://$https/ trusted through that root alone, with no name sent, as for an IP address (D116)"
    out="$(served_cert "$https" -noservername)"
    echo "$out"
    [[ "$out" == *"IP Address:$lan"* ]] || fail "the certificate does not name $lan"
    code="$(curl -sS --cacert "$work/root.crt" -o /dev/null -w '%{http_code}' "https://$https/index.php")"
    echo "curl --cacert root.crt https://$https/index.php: $code"
    [[ "$code" == [23]?? ]] || fail "https://$https/ answered $code"
    say "another name gets a certificate from that root when first asked for (on_demand)"
    port="${https##*:}"
    code="$(curl -sS --cacert "$work/root.crt" --resolve "derby.lan:$port:$lan" -o /dev/null \
        -w '%{http_code}' "https://derby.lan:$port/index.php")"
    echo "https://derby.lan:$port/index.php: $code"
    [[ "$code" == [23]?? ]] || fail "derby.lan answered $code"
    say "Caddy's log warns once about on_demand with no protections"
    out="$("${compose[@]}" logs caddy 2>&1)"
    echo "$out" | grep 'derbynet-caddy'
    [ "$(grep -c 'on-demand TLS is enabled, but no protections are in place' <<<"$out")" = 1 ] \
        || fail "not one on_demand warning"

    say "plain HTTP is served, with no redirect to HTTPS"
    code="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://$http/action.php?query=role.list")"
    echo "$code"
    case "$code" in *https:*) fail "plain HTTP redirects to HTTPS: $code" ;; [23]??*) ;; *) fail "plain HTTP answered $code" ;; esac

    say "a post with an https Origin passes over https, and over http with a forged X-Forwarded-Proto it does not"
    code="$(curl -s --cacert "$work/root.crt" -H "Origin: https://$https" -d 'action=role.login&name=' \
        "https://$https/action.php")"
    if grep -q notauthorized <<<"$code"; then fail "Django took HTTPS through Caddy for HTTP"; fi
    echo "https: passed"
    code="$(origin_post "http://$http" -H 'X-Forwarded-Proto: https')"
    echo "http, forged: $code"
    [ "$code" = notauthorized ] || fail "Django took a client's X-Forwarded-Proto through Caddy"

    say "kiosk.php through Caddy sees the client's address, $lan, over http and https (D118)"
    kiosk_address "http://$http" "$lan" >/dev/null
    kiosk_address "https://$https" "$lan" --cacert "$work/root.crt" >/dev/null
    say "and not the X-Forwarded-For, X-Real-IP or X-Real-Port a client sends"
    port="$(kiosk_address "http://$http" "$lan" -H 'X-Forwarded-For: 10.9.8.7' -H 'X-Real-IP: 10.9.8.6' \
        -H 'X-Real-Port: 4242')"
    [ "$port" != 4242 ] || fail "a client's X-Real-Port reached Django"
    "${compose[@]}" down --volumes

    say "tls/ holding cert.pem alone: Caddy's own authority, and one line naming it (D119)"
    "${compose[@]}" create
    docker run --rm -v "${project}_data:/d" --entrypoint sh "$IMAGE" -c \
        'mkdir -p /d/tls && echo half > /d/tls/cert.pem'
    "${compose[@]}" up -d --wait
    https="$lan:$("${compose[@]}" port caddy 443 | sed 's/.*://')"
    http="$lan:$("${compose[@]}" port caddy 80 | sed 's/.*://')"
    out="$("${compose[@]}" logs caddy 2>&1)"
    echo "$out" | grep 'derbynet-caddy'
    grep -q "tls/ holds cert.pem alone; using Caddy's own certificate authority" <<<"$out" \
        || fail "no line names the missing half"
    curl -fsS -o "$work/root2.crt" "http://$http/derbynet-ca.crt"
    curl -fsS --cacert "$work/root2.crt" -o /dev/null "https://$https/index.php" \
        || fail "Caddy's own certificate was not served"
    "${compose[@]}" down --volumes

    say "a DERBYNET_ADDRESS that is no address stops Caddy with a message"
    status=0
    out="$(DERBYNET_ADDRESS='192.168.1.20;x' "${compose[@]}" run --rm --no-deps -T caddy 2>&1)" || status=$?
    echo "$out" | tail -1
    echo "exit status $status"
    [ "$status" != 0 ] || fail "Caddy started"
    [[ "$out" == *"DERBYNET_DOMAIN and DERBYNET_ADDRESS hold names and addresses, not '192.168.1.20;x'"* ]] \
        || fail "no message names the value"
    "${compose[@]}" down --volumes
    unset DERBYNET_ADDRESS
}

# put_pair <volume> <dir>: copies <dir>/cert.pem and key.pem into the
# volume's tls/, as a user puts their own there.
put_pair() {
    docker run --rm -v "$1:/d" -v "$2:/pair:ro" --entrypoint sh "$IMAGE" -c \
        'mkdir -p /d/tls && cp /pair/cert.pem /pair/key.pem /d/tls/'
}

# expired_pair <dir>: an expired self-signed pair in <dir>, made on the host.
expired_pair() {
    mkdir -p "$1"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj /CN=expired \
        -not_before 20200101000000Z -not_after 20200201000000Z \
        -keyout "$1/key.pem" -out "$1/cert.pem" 2>/dev/null
    chmod 644 "$1/key.pem"
}

# A volume the last image before D107 made keeps its certificate under
# compose.yaml's Caddy, so no phone trusts again (D107, D119).
check_old_cert() {
    local project="$PREFIX-oldcert" before after named https
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml")
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    "${compose[@]}" create
    say "a volume $OLD_TLS_IMAGE made"
    before="$(old_tls_volume "${project}_data")"
    say "the same volume under compose.yaml, with DERBYNET_UID=$(id -u) DERBYNET_GID=$(id -g)"
    DERBYNET_UID="$(id -u)" DERBYNET_GID="$(id -g)" "${compose[@]}" up -d --wait
    https="$("${compose[@]}" port caddy 443)"
    "${compose[@]}" logs caddy 2>&1 | grep 'derbynet-caddy'
    after="$(served_cert "$https" | sed -n 1p)"
    named="$(served_cert "$https" -servername derby.lan | sed -n 1p)"
    echo "before:               $before"
    echo "after, no name:       $after"
    echo "after, for derby.lan: $named"
    [ "$before" = "$after" ] && [ "$before" = "$named" ] || fail "Caddy serves another certificate"
    say "tls/ is the host user's, and key.pem readable by its owner alone (D119)"
    "${compose[@]}" exec -T server stat -c '%u:%g %a %n' /var/lib/derbynet/tls/cert.pem /var/lib/derbynet/tls/key.pem \
        | tee "$RUNNER_TEMP_DIR/oldcert-stat.txt"
    grep -qx "$(id -u):$(id -g) 600 /var/lib/derbynet/tls/key.pem" "$RUNNER_TEMP_DIR/oldcert-stat.txt" \
        || fail "key.pem is not the host user's, mode 0600"
    grep -q "^$(id -u):$(id -g) [0-7]* /var/lib/derbynet/tls/cert.pem$" "$RUNNER_TEMP_DIR/oldcert-stat.txt" \
        || fail "cert.pem is not the host user's"
    "${compose[@]}" down

    say "an expired pair in tls/ is served as it is (D119)"
    expired_pair "$RUNNER_TEMP_DIR/expired"
    before="$(openssl x509 -noout -fingerprint -sha256 -in "$RUNNER_TEMP_DIR/expired/cert.pem")"
    put_pair "${project}_data" "$RUNNER_TEMP_DIR/expired"
    "${compose[@]}" up -d --wait
    https="$("${compose[@]}" port caddy 443)"
    after="$(served_cert "$https" | sed -n 1p)"
    echo "expired: $before"
    echo "served:  $after"
    [ "$before" = "$after" ] || fail "Caddy does not serve the expired pair"
    "${compose[@]}" down --volumes
}

# readme_block <lang> <word> <file>: the body of the first ```<lang> block of
# the file that holds <word>.
readme_block() {
    awk -v lang="$1" -v word="$2" '
        $0 == "```" lang {on=1; block=""; next}
        /^```$/ {if (on && !done && index(block, word)) {printf "%s", block; done=1}; on=0; next}
        on {block = block $0 "\n"}' "$3"
}

# The top-level README's Compose example and its commands, run as written
# (D107): the file, saved as docker-compose.yml, and the commands that write
# .env and start it.  Three things come from the run, not the README: the
# project name gets this run's prefix, the image is the one under test, and
# README_HTTP_PORT and README_HTTPS_PORT, when the caller sets them, become
# DERBYNET_HTTP_PORT and DERBYNET_HTTPS_PORT, to move the ports off 80 and
# 443 on a computer where those are taken.  CI sets neither.
check_readme_compose() {
    local work="$RUNNER_TEMP_DIR/readme" project="$PREFIX-readme" lines lan http https code
    compose_version_at_least 2.23.1 \
        || fail "docker compose is older than 2.23.1, which compose.yaml's configs: content needs"
    unset DERBYNET_HTTP_PORT DERBYNET_HTTPS_PORT DERBYNET_ADDRESS
    [ -z "${README_HTTP_PORT:-}" ] || export DERBYNET_HTTP_PORT="$README_HTTP_PORT"
    [ -z "${README_HTTPS_PORT:-}" ] || export DERBYNET_HTTPS_PORT="$README_HTTPS_PORT"
    mkdir -p "$work"
    readme_block yaml services: "$ROOT/README.md" > "$work/docker-compose.yml"
    [ -s "$work/docker-compose.yml" ] || fail "README.md holds no Compose example"
    lines="$(readme_block sh 'docker compose up' "$ROOT/README.md")"
    [ -n "$lines" ] || fail "README.md has no commands that start the example"
    say "the README's commands, in the folder holding its docker-compose.yml"
    echo "$lines"
    (cd "$work" && COMPOSE_PROJECT_NAME="$project" DERBYNET_IMAGE="$IMAGE" bash -euo pipefail -c "$lines")
    cat "$work/.env"
    local compose=(docker compose -p "$project" -f "$work/docker-compose.yml")
    export DERBYNET_IMAGE="$IMAGE"
    "${compose[@]}" up -d --wait
    lan="$(sed -n 's/^DERBYNET_ADDRESS=//p' "$work/.env")"
    [ "$lan" = "$(lan_address)" ] || fail ".env's DERBYNET_ADDRESS is not $(lan_address)"
    http="$lan:$("${compose[@]}" port caddy 80 | sed 's/.*://')"
    https="$lan:$("${compose[@]}" port caddy 443 | sed 's/.*://')"
    say "HTTPS at https://$https/, trusted through Caddy's root from http://$http/derbynet-ca.crt"
    curl -fsS -o "$work/root.crt" "http://$http/derbynet-ca.crt"
    code="$(curl -sS --cacert "$work/root.crt" -o /dev/null -w '%{http_code}' "https://$https/index.php")"
    echo "$code"
    [[ "$code" == [23]?? ]] || fail "https://$https/ answered $code"
    say "the server runs as the host user, in the .env's time zone"
    "${compose[@]}" exec -T server sh -c "! find /var/lib/derbynet \\( ! -uid $(id -u) -o ! -gid $(id -g) \\) -print | grep ." \
        || fail "files on the volume are not the host user's"
    [ "$("${compose[@]}" exec -T server printenv TZ)" = America/Chicago ] || fail "TZ is not .env's"
    "${compose[@]}" down --volumes
}

# DERBYNET_DOMAIN gets a certificate from an ACME server (D119, D121):
# Pebble, through server/docker/ci/compose.pebble.yaml, stands in for
# Let's Encrypt.
check_acme() {
    local project="$PREFIX-acme" https out
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml"
                   -f "$ROOT/server/docker/ci/compose.pebble.yaml")
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    say "DERBYNET_DOMAIN=derby.test, DERBYNET_ACME_CA=https://pebble:14000/dir, over a pair in tls/ (D119)"
    "${compose[@]}" create
    expired_pair "$RUNNER_TEMP_DIR/acme-pair"
    put_pair "${project}_data" "$RUNNER_TEMP_DIR/acme-pair"
    "${compose[@]}" up -d --wait
    for _ in $(seq 60); do
        out="$("${compose[@]}" logs caddy 2>&1)"
        grep -q '"certificate obtained successfully","identifier":"derby.test"' <<<"$out" && break
        sleep 1
    done
    grep -E 'derbynet-caddy|certificate obtained' <<<"$out" || true
    https="$("${compose[@]}" port caddy 443)"
    out="$(openssl s_client -connect "$https" -servername derby.test </dev/null 2>/dev/null \
        | openssl x509 -noout -issuer -ext subjectAltName)"
    echo "$out"
    [[ "$out" == *"issuer=CN=Pebble Intermediate CA"* && "$out" == *"DNS:derby.test"* ]] \
        || fail "Caddy serves no certificate from Pebble for derby.test"
    "${compose[@]}" down --volumes
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

# The legacy database of transfer/tests/fixtures/legacy/v10.sql, with its photo
# folders, as database.md's "A whole event with its photos" copies one in.
legacy_folder() { # legacy_folder <dir>
    local fixtures="$ROOT/server/transfer/tests/fixtures/legacy"
    rm -rf "$1" && mkdir -p "$1"
    cp -r "$fixtures/v10-photos/." "$1/"
    python3 -I -c 'import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.executescript(open(sys.argv[2]).read()); c.commit()' \
        "$1/derbynet.sqlite3" "$fixtures/v10.sql"
    chmod -R a+rX "$1"
}

# The database a `docker compose exec -u derbynet ... python manage.py`
# reaches (#308).
exec_database() { # exec_database <compose...>: "<vendor> <name> <host>"
    "$@" exec -T -u derbynet server python manage.py shell --no-imports -c '
from django.db import connection
s = connection.settings_dict
print(connection.vendor, s["NAME"], s["HOST"] or "-")'
}

# docker compose exec under the postgres profile reaches the server's
# Postgres: database.md's import lands in Postgres, migrations show applied,
# and no SQLite file appears on the volume, also once Postgres has stopped,
# when the command prints one message saying what to start (#308).  Without
# the profile, it reaches the server's SQLite.
check_exec_database() {
    local project="$PREFIX-execdb" password='p@ss:w/rd#%' work="$RUNNER_TEMP_DIR/execdb"
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml"
                   --profile postgres)
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    legacy_folder "$work/pack42"
    say "compose --profile postgres with POSTGRES_PASSWORD='$password'"
    POSTGRES_PASSWORD="$password" "${compose[@]}" up -d --wait
    local out status got
    new_event "http://$("${compose[@]}" port caddy 80)"

    say "database.md's import, run with docker compose exec"
    "${compose[@]}" cp "$work/pack42" server:/tmp/pack42
    status=0
    out="$("${compose[@]}" exec -T -u derbynet server python manage.py import_derbynet \
        /tmp/pack42/derbynet.sqlite3 --event "Pack 42 2025" 2>&1)" || status=$?
    echo "$out" | tail -5
    echo "exit status $status"
    if [ "$status" != 0 ]; then
        "${compose[@]}" exec -T server sh -c 'ls -l /var/lib/derbynet/*.sqlite3' || true
        fail "the import failed"
    fi
    got="$(exec_database "${compose[@]}")"
    echo "exec'd manage.py's database: $got"
    [ "$got" = "postgresql derbynet postgres" ] || fail "an exec'd manage.py does not reach the server's Postgres"
    say "psql in the postgres container: select name from core_event order by id"
    got="$("${compose[@]}" exec -T postgres psql -U derbynet -d derbynet -tAc 'select name from core_event order by id')"
    echo "$got"
    [ "$got" = $'checks\nPack 42 2025' ] \
        || fail "Postgres does not hold the server's event and then the import"
    say "manage.py showmigrations --plan, run with docker compose exec"
    out="$("${compose[@]}" exec -T -u derbynet server python manage.py showmigrations --plan)"
    echo "$(grep -c '^\[X\]' <<<"$out" || true) applied, $(grep -c '^\[ \]' <<<"$out" || true) unapplied"
    if grep -q '^\[ \]' <<<"$out"; then fail "an exec'd showmigrations sees unapplied migrations"; fi
    "${compose[@]}" exec -T server sh -c '! ls /var/lib/derbynet/*.sqlite3 2>/dev/null' \
        || fail "a SQLite database was made on the volume"

    say "an explicit DATABASE_URL still wins"
    got="$("${compose[@]}" exec -T -u derbynet -e DATABASE_URL=sqlite:////tmp/explicit.sqlite3 server \
        python manage.py shell --no-imports -c '
from django.db import connection
print(connection.vendor, connection.settings_dict["NAME"])')"
    echo "$got"
    [ "$got" = "sqlite /tmp/explicit.sqlite3" ] || fail "DATABASE_URL did not win"

    say "a config folder with no database-engine, as before a volume's first start"
    got="$("${compose[@]}" exec -T -u derbynet -e DERBYNET_CONFIG_DIR=/tmp/no-engine server \
        python manage.py shell --no-imports -c '
from django.db import connection
print(connection.vendor, connection.settings_dict["NAME"])')"
    echo "$got"
    [ "$got" = "sqlite /var/lib/derbynet/derbynet.sqlite3" ] || fail "no database-engine did not keep SQLite"

    say "with the postgres container stopped, an exec'd manage.py fails with one message"
    "${compose[@]}" stop postgres
    status=0
    out="$("${compose[@]}" exec -T -u derbynet server python manage.py import_derbynet \
        /tmp/pack42/derbynet.sqlite3 --event "Pack 42 2026" 2>&1)" || status=$?
    echo "$out" | tail -4
    echo "exit status $status, $(wc -l <<<"$out") lines"
    [ "$status" != 0 ] || fail "the import succeeded with Postgres stopped"
    [[ "$out" == "derbynet: could not connect to the Postgres at postgres: "*"Start it with 'docker compose --profile postgres up -d', or check DERBYNET_POSTGRES_HOST." ]] \
        || fail "no short message names the host and what to start"
    [[ "$out" != *Traceback* ]] || fail "the command printed a traceback"
    [[ "$out" != *"$password"* ]] || fail "the error shows the password"
    "${compose[@]}" exec -T server sh -c '! ls /var/lib/derbynet/*.sqlite3 2>/dev/null' \
        || fail "a SQLite database was made on the volume"
    say "the server's log and docker inspect hold no database URL"
    out="$("${compose[@]}" logs server 2>&1)"
    [[ "$out" != *"postgres://"* ]] || fail "the server's log holds a database URL"
    out="$(docker inspect "$("${compose[@]}" ps -q server)")"
    [[ "$out" != *"DATABASE_URL"* ]] || fail "docker inspect shows a DATABASE_URL"
    "${compose[@]}" down --volumes

    say "compose with no profile: an exec'd manage.py reaches the server's SQLite"
    compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml")
    "${compose[@]}" up -d --wait
    new_event "http://$("${compose[@]}" port caddy 80)"
    got="$(exec_database "${compose[@]}")"
    echo "exec'd manage.py's database: $got"
    [ "$got" = "sqlite /var/lib/derbynet/derbynet.sqlite3 -" ] || fail "an exec'd manage.py does not reach SQLite"
    got="$("${compose[@]}" exec -T -u derbynet server python manage.py shell --no-imports -c '
from core.models import Event
print(*Event.objects.values_list("name", flat=True))')"
    echo "events: $got"
    [ "$got" = checks ] || fail "an exec'd manage.py does not see the server's event"
    "${compose[@]}" down --volumes
}

# `docker exec` and `docker compose exec` run as root unless given -u, and in
# the image manage.py then refuses to run (D103): an import run as root leaves
# the event's photo folder root's (#327's review).  The refused command never
# starts, the build's collectstatic still runs as root, and the same command
# with -u derbynet runs.  Each exec'd manage.py here without -u carries
# ROOT_ON_PURPOSE (tests/test_docker_checks.py).
check_root() {
    local project="$PREFIX-root"
    local compose=(docker compose -p "$project" -f "$ROOT/server/docker/compose.yaml")
    export DERBYNET_IMAGE="$IMAGE" DERBYNET_HTTP_PORT=127.0.0.1:0 DERBYNET_HTTPS_PORT=127.0.0.1:0
    # A changed uid, so -u derbynet and the refusal's advice are checked at
    # the uid the server runs as (D106).
    DERBYNET_UID=2345 DERBYNET_GID=2346 "${compose[@]}" up -d --wait
    local out status
    say "manage.py check, run with docker compose exec and no -u"
    status=0
    out="$("${compose[@]}" exec -T server python manage.py check 2>&1)" || status=$?  # root on purpose (D103)
    echo "$out"
    echo "exit status $status"
    [ "$status" != 0 ] || fail "manage.py check ran as root"
    [ "$out" = "derbynet: manage.py check refuses to run as root. Run it as the server's user: pass -u derbynet to docker exec or docker compose exec." ] \
        || fail "manage.py check as root did not print one line naming the command and -u derbynet"

    say "a refused manage.py shell writes nothing"
    status=0
    out="$("${compose[@]}" exec -T server python manage.py shell --no-imports -c 'open("/var/lib/derbynet/ran-as-root", "w")' 2>&1)" || status=$?  # root on purpose (D103)
    echo "$out"
    echo "exit status $status"
    [ "$status" != 0 ] || fail "manage.py shell ran as root"
    "${compose[@]}" exec -T server sh -c '! ls -l /var/lib/derbynet/ran-as-root 2>/dev/null' \
        || fail "the refused command wrote /var/lib/derbynet/ran-as-root"

    say "the build's collectstatic still runs as root"
    status=0
    out="$("${compose[@]}" exec -T server python manage.py collectstatic --noinput --dry-run --verbosity 1 2>&1)" || status=$?  # root on purpose (D103)
    echo "$out" | tail -1
    echo "exit status $status"
    [ "$status" = 0 ] || fail "collectstatic did not run as root"

    say "docker compose exec -u derbynet server sh -c 'id -u; python manage.py check'"
    status=0
    out="$("${compose[@]}" exec -T -u derbynet server sh -c 'id -u; python manage.py check' 2>&1)" || status=$?
    echo "$out"
    echo "exit status $status"
    [ "$status" = 0 ] || fail "manage.py check failed as derbynet"
    [[ "$out" == $'2345\nSystem check identified no issues'* ]] || fail "manage.py check did not run as derbynet, uid 2345"
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
    grep -q success <<<"$(curl -sf -d 'action=setup.nodata&ez-new=checks' "$1/action.php")" \
        || fail "setup.nodata did not make an event at $1"
}

login() { # login <url> <jar> <role>
    local password
    password="$(sed -n -e "s/^$3://p" "$TESTING/default.passwords")"
    grep -q success <<<"$(curl -sf -c "$2" -b "$2" \
        -d "action=role.login&name=$3&password=$password" "$1/action.php")" \
        || fail "$3 could not log in"
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
    if grep -q '^\[ \]' <<<"$(docker exec -u derbynet "$PREFIX-new1" python manage.py showmigrations --plan)"; then
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
    local url="http://$("${compose[@]}" port caddy 80)"
    new_event "$url"
    (cd "$work" && bash testing/reset-database.sh "$url" && bash testing/import-roster.sh "$url") \
        >"$work/populate.log" 2>&1 || { tail "$work/populate.log"; fail "no event"; }
    local jar="$work/cookies" racer
    login "$url" "$jar" RaceCoordinator
    racer="$(curl -s -b "$jar" "$url/action.php?query=racer.list" | jq -r '.racers[0].racerid')"
    grep -q success <<<"$(curl -s -b "$jar" -F action=photo.upload -F repo=head -F racerid="$racer" \
        -F photo=@"$(first "$TESTING"/data/headshots/*.jpg)" "$url/action.php")" \
        || fail "no racer photo"
    grep -q success <<<"$(curl -s -b "$jar" -F action=photo.upload -F repo=car -F racerid="$racer" \
        -F photo=@"$(first "$TESTING"/data/carphotos/Car-*.jpg)" "$url/action.php")" \
        || fail "no car photo"
    head -c 300000 /dev/urandom > "$work/replay.mkv"
    grep -q success <<<"$(curl -s -b "$jar" -F action=video.upload \
        -F video=@"$work/replay.mkv;filename=Heat-1.mkv" "$url/action.php")" || fail "no video"
    local before after
    before="$(backup_state "$url" "$jar" "$racer")"
    echo "$before"

    say "backup: stop the server, copy the volumes, start it again"
    local data caddy postgres
    data="$("${compose[@]}" volumes -q | grep '_data$')"
    caddy="$("${compose[@]}" volumes -q | grep '_caddy$')"
    postgres="$("${compose[@]}" volumes -q | grep '_postgres$' || true)"
    "${compose[@]}" stop
    (
        cd "$work"
        umask 077
        docker run --rm -v "$data:/data:ro" --entrypoint tar "$IMAGE" -C /data -czf - . > derbynet-data.tgz
        docker run --rm -v "$caddy:/data:ro" --entrypoint tar "$IMAGE" -C /data -czf - . > derbynet-caddy.tgz
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
    docker run --rm -i -v "$caddy:/data" --entrypoint tar "$IMAGE" -C /data -xzf - < "$work/derbynet-caddy.tgz"
    if [ "$profile" = postgres ]; then
        docker run --rm -i -v "$postgres:/data" --entrypoint tar "$IMAGE" \
            -C /data -xzf - < "$work/derbynet-postgres.tgz"
    fi
    "${compose[@]}" up -d --wait
    url="http://$("${compose[@]}" port caddy 80)"
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
    # Caddy's certificate authority (D120): a restore that made a new one
    # would have every phone install it again.
    echo "Caddy's root $(curl -fsS "$1/derbynet-ca.crt" | openssl x509 -noout -fingerprint -sha256)"
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

# The page's own files: each <script src>, <img src> and stylesheet href,
# whichever order the attributes come in and whichever quotes they use.  The
# page is read as one line, since a tag may span lines.
page_files() { # page_files <html file>
    local page tags
    page="$(tr '\n\t' '  ' < "$1")"
    tags="$(grep -oiE '<(script|img|link)[[:space:]][^>]*>' <<<"$page" || true)"
    tags="$(grep -viE '^<link' <<<"$tags" || true)
$(grep -iE "^<link.*[[:space:]]rel=[\"']?stylesheet" <<<"$tags" || true)"
    sed -nE "s/.*[[:space:]](src|href)=[\"']([^\"']+)[\"'].*/\2/p" <<<"$tags"
}

# The files a script loads by a quoted relative path, in either quote style
# and escaped too: main.js's clock worker, and the images gui.js and main.js
# draw.
script_files() { # script_files <script file>
    grep -oE '\\?["'"'"'](js|img)/[A-Za-z0-9_./-]+\.(js|png)\\?["'"'"']' "$1" \
        | tr -d "\"'\\\\" || true
}

# The files the timer's scripts load, named here so that a missing script
# cannot hide them: main.js's clock worker, the images main.js and gui.js
# draw, and the four serial status images, whose names gui.js builds
# (`'img/status/' + png + '.png'`).
TIMER_SCRIPT_FILES="js/timer/clock-worker.js img/small-check.png img/small-cross.png
img/timer/trouble-tiny.png img/status/ok.png img/status/not_connected.png
img/status/trouble.png img/status/unknown.png"

# The browser timer (#312): /timer.php, as RaceCoordinator, the race-day
# laptop's login (D53), at the root and under the Debian layout's /derbynet/.
# Every file the page links, TIMER_SCRIPT_FILES, and every file a script it
# links names by a quoted path answer 200 with their content type.  Each
# file's status is printed before the check fails, so the log lists them all.
check_timer() {
    start timer "$PREFIX-timer" -p 127.0.0.1::80
    local url="http://$(docker port "$PREFIX-timer" 80/tcp)" jar="$RUNNER_TEMP_DIR/timer-cookies"
    local html="$RUNNER_TEMP_DIR/timer.html" script="$RUNNER_TEMP_DIR/timer-script"
    new_event "$url"
    login "$url" "$jar" RaceCoordinator
    local base got files path want bad=0 seen
    for base in / /derbynet/; do
        say "${base}timer.php as RaceCoordinator"
        got="$(curl -s -o "$html" -b "$jar" -w '%{http_code} %{content_type}' "$url${base}timer.php")"
        echo "$got"
        case "$got" in "200 text/html"*) ;; *) fail "${base}timer.php answered $got" ;; esac
        files="$(page_files "$html")"
        while read -r path; do
            case "$path" in *.js) ;; *) continue ;; esac
            curl -s -o "$script" -b "$jar" "$url$base$path"
            files+=$'\n'"$(script_files "$script")"
        done <<<"$files"
        files+=$'\n'"$(tr ' ' '\n' <<<"$TIMER_SCRIPT_FILES")"
        files="$(awk 'NF && !seen[$0]++' <<<"$files")"
        seen=0
        while read -r path; do
            case "$path" in /*|*://*) fail "${base}timer.php loads $path, not a relative path" ;; esac
            got="$(curl -s -o /dev/null -b "$jar" -w '%{http_code} %{content_type}' "$url$base$path")"
            echo "$got $base$path"
            seen=$((seen + 1))
            case "$path:$got" in
                *.js:"200 text/javascript"*|*.js:"200 application/javascript"*) ;;
                *.css:"200 text/css"*) ;;
                *.png:"200 image/png"*) ;;
                *) bad=$((bad + 1)) ;;
            esac
        done <<<"$files"
        echo "$seen files"
        for want in js/timer/main.js js/timer/profiles.js css/timer.css js/jquery.js \
                    img/gear_icon.png img/derbynet.png; do
            grep -qxF "$want" <<<"$files" || fail "${base}timer.php links no $want"
        done
    done
    [ "$bad" = 0 ] || fail "$bad of the timer page's files did not answer 200 with their type"
}

RUNNER_TEMP_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/$PREFIX.XXXXXX")"
trap 'cleanup; rm -rf "$RUNNER_TEMP_DIR"' EXIT
[ $# -gt 0 ] || set -- tls secret user upload bind-mount uid-env uid-root uid-takeover \
    uid-exec client-ip forwarded-proto stop \
    reload timeout engine postgres-password exec-database root bad-env allowed-hosts docs timer \
    caddy old-cert readme-compose acme
for check in "$@"; do
    case "$check" in
        tls) check_tls ;;
        secret) check_secret ;;
        user) check_user ;;
        upload) check_upload ;;
        bind-mount) check_bind_mount ;;
        uid-env) check_uid_env ;;
        uid-root) check_uid_root ;;
        uid-takeover) check_uid_takeover ;;
        uid-exec) check_uid_exec ;;
        client-ip) check_client_ip ;;
        timeout) check_timeout ;;
        engine) check_engine ;;
        forwarded-proto) check_forwarded_proto ;;
        stop) check_stop ;;
        reload) check_reload ;;
        postgres-password) check_postgres_password ;;
        exec-database) check_exec_database ;;
        root) check_root ;;
        bad-env) check_bad_env ;;
        allowed-hosts) check_allowed_hosts ;;
        upgrade) check_upgrade ;;
        backup) check_backup ;;
        caddy) check_caddy ;;
        old-cert) check_old_cert ;;
        readme-compose) check_readme_compose ;;
        acme) check_acme ;;
        docs) check_docs ;;
        timer) check_timer ;;
        *) fail "no check named $check" ;;
    esac
    cleanup
    echo "ok: $check"
done
