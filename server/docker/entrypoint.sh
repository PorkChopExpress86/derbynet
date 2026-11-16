#!/bin/bash
#
# The image's entrypoint (#58), run by tini as root.  It gives the derbynet
# user the host user's uid and gid (D106), makes the data folders writable by
# derbynet, writes nginx's configuration from the environment, runs the
# migrations as derbynet, then starts gunicorn as derbynet and nginx.  The
# image serves plain HTTP on port 80 alone; HTTPS comes from a proxy in front
# of it, such as compose.yaml's Caddy (D107).
# It exits as soon as either of the two exits, so Docker's restart policy can
# start the container again.  server/docker/README.md lists the variables.

set -euo pipefail

log() { echo "derbynet: $*" >&2; }
die() { log "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "the entrypoint must start as root; it runs the server as the derbynet user"

export DERBYNET_DATA_DIR="${DERBYNET_DATA_DIR:-/var/lib/derbynet}"
export DERBYNET_CONFIG_DIR="${DERBYNET_CONFIG_DIR:-/var/lib/derbynet}"
# The one place each default is set; server/docker/README.md lists them.
MAX_UPLOAD_SIZE="${MAX_UPLOAD_SIZE:-100M}"
DERBYNET_REQUEST_TIMEOUT="${DERBYNET_REQUEST_TIMEOUT:-30}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-4}"
export MAX_UPLOAD_SIZE

# The values that go into nginx's configuration are checked here, so a typo
# can't inject a directive.  settings.py checks MAX_UPLOAD_SIZE the same way.
[[ "$MAX_UPLOAD_SIZE" =~ ^[0-9]+[kKmMgG]?$ && ! "$MAX_UPLOAD_SIZE" =~ ^0+[kKmMgG]?$ ]] \
    || die "MAX_UPLOAD_SIZE must be a size such as 16M, 512k or 1G, not '$MAX_UPLOAD_SIZE'"
[[ "$DERBYNET_REQUEST_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
    || die "DERBYNET_REQUEST_TIMEOUT must be a whole number of seconds, not '$DERBYNET_REQUEST_TIMEOUT'"
[[ "$GUNICORN_WORKERS" =~ ^[1-9][0-9]*$ ]] \
    || die "GUNICORN_WORKERS must be a whole number, not '$GUNICORN_WORKERS'"
for name in DERBYNET_DATA_DIR DERBYNET_CONFIG_DIR; do
    [[ "${!name}" =~ ^/[A-Za-z0-9._/-]*$ ]] \
        || die "$name must be an absolute path of letters, digits and . _ - /, not '${!name}'"
done
# Client addresses or networks whose X-Forwarded-Proto is believed: a proxy in
# front of this container.  From anyone else it is replaced by the scheme of
# the connection.
trusted="${DERBYNET_TRUSTED_PROXIES:-}"
trusted_geo=""
for net in ${trusted//,/ }; do
    [[ "$net" =~ ^[0-9A-Fa-f.:]+(/[0-9]{1,3})?$ ]] \
        || die "DERBYNET_TRUSTED_PROXIES holds addresses or networks such as 172.17.0.1 or 10.0.0.0/8, not '$net'"
    trusted_geo+="        $net 1;"$'\n'
done
prefix="${DERBYNET_URL_PREFIX:-}"
prefix="${prefix#"${prefix%%[!/]*}"}"
prefix="${prefix%"${prefix##*[!/]}"}"
[[ "$prefix" =~ ^[A-Za-z0-9._~/-]*$ ]] \
    || die "DERBYNET_URL_PREFIX may hold only letters, digits and . _ ~ - /, not '$DERBYNET_URL_PREFIX'"

# --- the server's uid and gid (D106) -----------------------------------------
# derbynet runs as the host user's uid and gid, so the data folder's files
# belong to that user on a bind mount and a named volume alike.
# DERBYNET_UID and DERBYNET_GID set them.  When both are unset and the data
# folder belongs to a user other than root, as a bind mount the user made
# does, derbynet takes that owner.  Otherwise it keeps the image's own ids,
# which the Dockerfile writes to /opt/derbynet/derbynet-ids.  An empty value,
# as compose.yaml passes for a line .env lacks, counts as unset.

# derbynet_ids <owner uid> <owner gid> <image uid> <image gid>: prints the uid
# and gid derbynet runs as, from DERBYNET_UID and DERBYNET_GID or the data
# folder's owner, or stops with one line.
derbynet_ids() {
    local uid="${DERBYNET_UID:-}" gid="${DERBYNET_GID:-}" name value
    # One without the other stops (D108): the guide always sets both, and a
    # default gid would put the files in a group the host user is not in.
    if [ -n "$uid" ] && [ -z "$gid" ]; then
        die "DERBYNET_GID is unset; set both DERBYNET_UID and DERBYNET_GID, or neither."
    fi
    if [ -z "$uid" ] && [ -n "$gid" ]; then
        die "DERBYNET_UID is unset; set both DERBYNET_UID and DERBYNET_GID, or neither."
    fi
    if [ -z "$uid" ]; then
        uid="$3" gid="$4"
        if [ "$1" != 0 ]; then
            uid="$1"
            # A user's folder in root's group, as 'sudo mkdir' and then
            # 'sudo chown <user>' leave it, keeps the image's gid (D109).
            # The refusal of gid 0 is for DERBYNET_GID alone.
            [ "$2" = 0 ] || gid="$2"
        fi
        echo "$uid $gid"
        return 0
    fi
    for name in DERBYNET_UID DERBYNET_GID; do
        value="${!name}"
        # 4294967294 is the largest id usermod and groupmod take; 4294967295
        # is (uid_t)-1, which means no id.
        [[ "$value" =~ ^[0-9]{1,10}$ ]] && (( 10#$value <= 4294967294 )) \
            || die "$name must be a whole number from 1 to 4294967294, as 'id -u' and 'id -g' print them, not '$value'"
        (( 10#$value != 0 )) \
            || die "$name=$value is root's id, and the image refuses to run the server as root. Set it to your own user's, as 'id -u' and 'id -g' print them."
    done
    echo "$((10#$uid)) $((10#$gid))"
}

# takeover <owner uid> <owner gid> <uid> <gid> <folder>: logs one line for a
# folder whose files go to another uid or gid, as after an upgrade or a
# changed DERBYNET_UID.  Nothing for a folder already derbynet's, or for an
# empty one, such as a new volume (D110).  A folder with files logs it, a
# root-owned one too.
takeover() {
    [ "$1:$2" != "$3:$4" ] || return 0
    [ -n "$(ls -A "$5")" ] || return 0
    log "the files under $5 belong to uid $1 (gid $2); giving them to uid $3 (gid $4), once, which takes longer the more photos the folder holds"
}

mkdir -p "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR"
# shellcheck disable=SC2046 # each stat and the file print two words
ids="$(derbynet_ids $(stat -c '%u %g' "$DERBYNET_DATA_DIR") $(cat /opt/derbynet/derbynet-ids))"
read -r uid gid <<<"$ids"
# -o: a uid or gid another account or group in the image holds, such as
# www-data's 33, is shared.  usermod -u would also re-own the files under
# derbynet's home, the data folder, unlogged and past the chown hint below,
# so the uid changes with the home pointed away, and then back.  The home
# goes back on every start whose home is elsewhere, as one is when a start
# was killed between the two calls; the Dockerfile's useradd sets it.
if [ "$(id -g derbynet)" != "$gid" ]; then
    groupmod -o -g "$gid" derbynet
fi
if [ "$(id -u derbynet)" != "$uid" ]; then
    usermod -o -u "$uid" -d /nonexistent derbynet
fi
if [ "$(getent passwd derbynet | cut -d: -f6)" != /var/lib/derbynet ]; then
    usermod -d /var/lib/derbynet derbynet
fi
# shellcheck disable=SC2046
takeover $(stat -c '%u %g' "$DERBYNET_DATA_DIR") "$uid" "$gid" "$DERBYNET_DATA_DIR"

# --- the data folders -------------------------------------------------------
# A bind mount can belong to any uid.  Give everything to derbynet, the tls/
# folder an image from before D107 made too, which compose.yaml's Caddy reads
# as root and serves (D107).  This runs on every start and gives only a file
# whose owner differs, such as one a root `docker exec` left, so a start with
# the same uid changes no file.  When chown can't (a
# share that squashes root, a container without CAP_CHOWN), say what to do
# rather than open the folder to everyone.
as_derbynet() { setpriv --reuid=derbynet --regid=derbynet --init-groups "$@"; }

find "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR" \
     \( \! -user derbynet -o \! -group derbynet \) -print0 \
  | xargs -0r chown -h derbynet:derbynet 2>/dev/null \
  || log "could not give every file under $DERBYNET_DATA_DIR to derbynet"
for dir in "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR"; do
    as_derbynet test -w "$dir" -a -x "$dir" || die "$dir is not writable by the derbynet user (uid $(id -u derbynet)). On a bind mount, run 'sudo chown -R $(id -u derbynet):$(id -g derbynet) <folder>' on the host, or use a named volume."
done

# --- nginx ------------------------------------------------------------------
# Plain HTTP on 80 with no redirect: kiosks, derby-timer.jar and the Pi
# scripts use http://.  No TLS: HTTPS is the proxy's (D107).  The log format
# has no $request_body, which held role.login's password in the PHP image
# (#64 S36).
prefix_locations=""
if [ -n "$prefix" ]; then
    prefix_locations="
        location = /$prefix { return 301 /$prefix/; }
        location /$prefix/static/ { alias /opt/derbynet/static/; }
        location /$prefix/ {
            include /etc/nginx/derbynet-proxy.conf;
            proxy_pass http://django/;
        }"
fi

cat > /etc/nginx/derbynet-proxy.conf <<EOF
proxy_set_header Host \$derbynet_host;
proxy_set_header X-Forwarded-Proto \$derbynet_proto;
proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP \$remote_addr;
proxy_set_header X-Real-Port \$remote_port;
proxy_read_timeout ${DERBYNET_REQUEST_TIMEOUT}s;
proxy_send_timeout ${DERBYNET_REQUEST_TIMEOUT}s;
proxy_redirect off;
EOF

cat > /etc/nginx/nginx.conf <<EOF
user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log stderr;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    server_tokens off;

    log_format derbynet '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                        '\$status \$body_bytes_sent "\$http_referer" '
                        '"\$http_user_agent" \$request_time';
    access_log /dev/stdout derbynet;

    client_max_body_size $MAX_UPLOAD_SIZE;

    # A trusted proxy in front of this one (DERBYNET_TRUSTED_PROXIES) says
    # the client's scheme; for anyone else it is this connection's.
    geo \$derbynet_trusted {
        default 0;
$trusted_geo    }
    map "\$derbynet_trusted:\$http_x_forwarded_proto" \$derbynet_proto {
        default \$scheme;
        "1:http" http;
        "1:https" https;
    }
    map \$http_host \$derbynet_host {
        "" \$host;
        default \$http_host;
    }

    upstream django {
        server unix:/run/derbynet/gunicorn.sock fail_timeout=0;
    }

    server {
        listen 80 default_server;
        absolute_redirect off;
$prefix_locations
        location /static/ { alias /opt/derbynet/static/; }
        location / {
            include /etc/nginx/derbynet-proxy.conf;
            proxy_pass http://django;
        }
    }
}
EOF
nginx -t -q -e stderr

# --- the database -----------------------------------------------------------
# DATABASE_URL, when set, is the database.  Otherwise compose.yaml's postgres
# service is, when DERBYNET_POSTGRES_HOST answers by name, which it does once
# compose's postgres profile runs that service.  Otherwise SQLite on the data
# volume.  A volume that has run on Postgres refuses to start on SQLite, which
# would show an empty event instead of the race.
# The engine chosen here goes into database-engine before any manage.py runs,
# and settings.py builds the postgres service's URL from that file and the
# POSTGRES_* variables, so gunicorn, migrate and a command started with
# `docker compose exec` all use this database (#308).  No URL is exported or
# logged, since it holds the password.
engine_file="$DERBYNET_CONFIG_DIR/database-engine"
case "${DATABASE_URL:-}" in
    postgres*) engine=postgres ;;
    "") engine=sqlite ;;
    *) engine=other ;;
esac
if [ -z "${DATABASE_URL:-}" ] && [ -n "${DERBYNET_POSTGRES_HOST:-}" ] \
   && getent hosts "$DERBYNET_POSTGRES_HOST" >/dev/null; then
    engine=postgres
    log "using Postgres at $DERBYNET_POSTGRES_HOST"
fi
# A volume keeps the engine it first ran on.  Starting it on the other one
# would show an empty event and leave the race in the first.
recorded="$(cat "$engine_file" 2>/dev/null || true)"
if [ -n "$recorded" ] && [ "$recorded" != "$engine" ]; then
    case "$recorded" in
        postgres) die "this volume's race data is in Postgres, and the server would start on $engine. Start it with 'docker compose --profile postgres up', or delete $engine_file to start again on $engine." ;;
        *) die "this volume's race data is in $recorded, and the server would start on $engine. Unset DATABASE_URL and start without the postgres profile, or delete $engine_file to start again on $engine." ;;
    esac
fi
echo "$engine" > "$engine_file"
chown derbynet:derbynet "$engine_file" 2>/dev/null || true

# --- Django -----------------------------------------------------------------
cd /opt/derbynet/server
log "applying migrations"
as_derbynet python manage.py migrate --noinput

# The socket's folder and the socket are derbynet's, and nginx's www-data is
# in derbynet's group (Dockerfile), so nginx can open the socket.
mkdir -p /run/derbynet
chown derbynet:derbynet /run/derbynet
chmod 0750 /run/derbynet
rm -f /run/derbynet/gunicorn.sock

# For development (README.md): restart the workers when a Python file or a
# template changes.  Django caches templates even with DEBUG on, and only
# runserver's reloader clears that cache, so each template is watched too.
reload=()
if [ "${GUNICORN_RELOAD:-0}" = 1 ]; then
    reload=(--reload)
    while IFS= read -r -d '' template; do
        reload+=(--reload-extra-file "$template")
    done < <(find /opt/derbynet/server -path '*/templates/*' -type f -print0)
fi

stopping=0
trap 'stopping=1' TERM INT

as_derbynet gunicorn derbynet.proxied_wsgi:application \
    --bind unix:/run/derbynet/gunicorn.sock --umask 007 \
    --workers "$GUNICORN_WORKERS" --timeout "$DERBYNET_REQUEST_TIMEOUT" \
    --error-logfile - --no-control-socket "${reload[@]}" &
gunicorn_pid=$!

for _ in $(seq 100); do
    [ -S /run/derbynet/gunicorn.sock ] && break
    kill -0 "$gunicorn_pid" 2>/dev/null || break
    sleep 0.1
done

nginx -e stderr -g 'daemon off;' &
nginx_pid=$!
log "serving plain HTTP on port 80${prefix:+, and under /$prefix/}"

status=0
wait -n "$gunicorn_pid" "$nginx_pid" || status=$?
if [ "$stopping" = 0 ]; then
    if kill -0 "$gunicorn_pid" 2>/dev/null; then
        log "nginx exited ($status); stopping"
    else
        log "gunicorn exited ($status); stopping"
    fi
    # An exit with status 0 is still unexpected, and on-failure restarts it.
    [ "$status" = 0 ] && status=1
fi
kill -TERM "$gunicorn_pid" "$nginx_pid" 2>/dev/null || true
# The two pids, not a bare wait, which after `wait -n` loops on "pid N is not
# a child of this shell" until docker stop's grace runs out.
wait "$gunicorn_pid" "$nginx_pid" 2>/dev/null || true
[ "$stopping" = 1 ] && exit 0
exit "$status"
