#!/bin/bash
#
# The image's entrypoint (#58), run by tini as root.  It makes the data
# folders writable by the derbynet user, creates the TLS key and certificate
# when they are missing, writes nginx's configuration from the environment,
# runs the migrations as derbynet, then starts gunicorn as derbynet and nginx.
# It exits as soon as either of the two exits, so Docker's restart policy can
# start the container again.  server/docker/README.md lists the variables.

set -euo pipefail

log() { echo "derbynet: $*" >&2; }
die() { log "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "the entrypoint must start as root; it runs the server as the derbynet user"

export DERBYNET_DATA_DIR="${DERBYNET_DATA_DIR:-/var/lib/derbynet}"
export DERBYNET_CONFIG_DIR="${DERBYNET_CONFIG_DIR:-/var/lib/derbynet}"
MAX_UPLOAD_SIZE="${MAX_UPLOAD_SIZE:-16M}"
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
prefix="${DERBYNET_URL_PREFIX:-}"
prefix="${prefix#"${prefix%%[!/]*}"}"
prefix="${prefix%"${prefix##*[!/]}"}"
[[ "$prefix" =~ ^[A-Za-z0-9._~/-]*$ ]] \
    || die "DERBYNET_URL_PREFIX may hold only letters, digits and . _ ~ - /, not '$DERBYNET_URL_PREFIX'"

TLS="$DERBYNET_CONFIG_DIR/tls"

# --- the data folders -------------------------------------------------------
# A bind mount can belong to any uid.  Give everything to derbynet but the TLS
# folder, which only nginx's root master reads.  When chown can't (a share
# that squashes root, a container without CAP_CHOWN), say what to do rather
# than open the folder to everyone.
as_derbynet() { setpriv --reuid=derbynet --regid=derbynet --init-groups "$@"; }

mkdir -p "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR"
find "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR" -path "$TLS" -prune -o \
     \( \! -user derbynet -o \! -group derbynet \) -print0 \
  | xargs -0r chown -h derbynet:derbynet 2>/dev/null \
  || log "could not give every file under $DERBYNET_DATA_DIR to derbynet"
for dir in "$DERBYNET_DATA_DIR" "$DERBYNET_CONFIG_DIR"; do
    as_derbynet test -w "$dir" -a -x "$dir" || die "$dir is not writable by the derbynet user (uid $(id -u derbynet)). On a bind mount, run 'sudo chown -R $(id -u derbynet):$(id -g derbynet) <folder>' on the host, or use a named volume."
done

# --- TLS --------------------------------------------------------------------
# Each install makes its own key on first start (#64 S35).  A cert.pem and
# key.pem put here by the user are used as they are.  A certificate this
# script made, whose fingerprint .generated holds, is made again once it has
# expired.
mkdir -p "$TLS"
chown root:root "$TLS"
chmod 0700 "$TLS"
if [ -e "$TLS/cert.pem" ] && [ -e "$TLS/key.pem" ]; then
    fingerprint="$(openssl x509 -noout -fingerprint -sha256 -in "$TLS/cert.pem" 2>/dev/null || true)"
    if [ -n "$fingerprint" ] && [ "$(cat "$TLS/.generated" 2>/dev/null)" = "$fingerprint" ] \
       && ! openssl x509 -checkend 0 -noout -in "$TLS/cert.pem" >/dev/null; then
        log "the generated TLS certificate has expired; making a new one"
        rm -f "$TLS/cert.pem" "$TLS/key.pem"
    fi
elif [ -e "$TLS/cert.pem" ] || [ -e "$TLS/key.pem" ]; then
    die "$TLS holds only one of cert.pem and key.pem. Add the other, or remove it to have a certificate made."
fi
if [ ! -e "$TLS/cert.pem" ]; then
    log "making a TLS key and self-signed certificate in $TLS"
    # 825 days, the longest validity Apple's devices accept for a TLS server
    # certificate (Apple support article HT210176, "Requirements for trusted
    # certificates in iOS 13 and macOS 10.15").
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -sha256 \
        -days 825 -nodes -subj "/CN=DerbyNet" \
        -addext "subjectAltName=DNS:derbynet,DNS:localhost,IP:127.0.0.1" \
        -keyout "$TLS/key.pem.new" -out "$TLS/cert.pem.new" 2>/dev/null
    chmod 0600 "$TLS/key.pem.new"
    mv "$TLS/key.pem.new" "$TLS/key.pem"
    mv "$TLS/cert.pem.new" "$TLS/cert.pem"
    openssl x509 -noout -fingerprint -sha256 -in "$TLS/cert.pem" > "$TLS/.generated"
fi
chmod 0600 "$TLS/key.pem"

# --- nginx ------------------------------------------------------------------
# Plain HTTP on 80 with no redirect: kiosks, derby-timer.jar and the Pi
# scripts use http://.  The log format has no $request_body, which held
# role.login's password in the PHP image (#64 S36).
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

    # A proxy in front of this one says the client's scheme; otherwise it is
    # this connection's.
    map \$http_x_forwarded_proto \$derbynet_proto {
        "" \$scheme;
        default \$http_x_forwarded_proto;
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
        listen 443 ssl default_server;
        ssl_certificate $TLS/cert.pem;
        ssl_certificate_key $TLS/key.pem;
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
# DATABASE_URL, when set, is the database.  Otherwise compose.yaml's
# DERBYNET_POSTGRES_URL is, when its host answers by name, which it does once
# compose's postgres profile runs the postgres service.  Otherwise SQLite on
# the data volume.  A volume that has run on Postgres refuses to start on
# SQLite, which would show an empty event instead of the race.
engine_file="$DERBYNET_CONFIG_DIR/database-engine"
if [ -z "${DATABASE_URL:-}" ] && [ -n "${DERBYNET_POSTGRES_URL:-}" ]; then
    pg_host="${DERBYNET_POSTGRES_URL#*@}"
    pg_host="${pg_host%%[:/]*}"
    if getent hosts "$pg_host" >/dev/null; then
        export DATABASE_URL="$DERBYNET_POSTGRES_URL"
        log "using Postgres at $pg_host"
    fi
fi
if [ -z "${DATABASE_URL:-}" ] && [ "$(cat "$engine_file" 2>/dev/null)" = postgres ]; then
    die "this volume's race data is in Postgres, and no Postgres is running. Start it with 'docker compose --profile postgres up', or delete $engine_file to start again on SQLite."
fi
case "${DATABASE_URL:-}" in
    postgres*) echo postgres > "$engine_file" ;;
    "") echo sqlite > "$engine_file" ;;
esac
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
log "serving on ports 80 and 443${prefix:+, and under /$prefix/}"

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
wait || true
[ "$stopping" = 1 ] && exit 0
exit "$status"
