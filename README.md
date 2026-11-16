# Please visit us at [https://derbynet.org](https://derbynet.org).

![icon](https://raw.githubusercontent.com/jeffpiazza/derbynet/master/website/img/derbynet-300.png)

# Run DerbyNet with Docker Compose

A race runs the server image with Caddy in front of it, which serves HTTPS
for the phone cameras and the browser timer (D107). `docs/install-docker.md`
walks through it step by step, and `server/docker/README.md` describes the
image. This is the whole setup in one file: save it as `docker-compose.yml`
in a folder of its own, write your settings into a file named `.env` beside
it, and start it from that folder. On Linux:

```sh
echo "DERBYNET_UID=$(id -u)" >> .env
echo "DERBYNET_GID=$(id -g)" >> .env
echo "DERBYNET_ADDRESS=$(hostname -I | cut -d' ' -f1)" >> .env
echo "TZ=America/Chicago" >> .env
docker compose up -d
```

Phones then open `https://<DERBYNET_ADDRESS>/`, after installing Caddy's
certificate authority from `http://<DERBYNET_ADDRESS>/derbynet-ca.crt`
(`docs/secure-contexts.md`). Kiosks and timers use `http://`, which is never
redirected. Add `--profile postgres` to every `docker compose` command to keep
the race in Postgres instead of SQLite. `server/docker/ci/checks.sh
readme-compose` runs this file and these commands as written.

```yaml
name: derbynet

# The database's name, user and password, for both containers.
# POSTGRES_PASSWORD may hold any character; the server percent-encodes it.
x-postgres: &postgres
  POSTGRES_USER: derbynet
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-derbynet}
  POSTGRES_DB: derbynet

services:
  server:
    image: ${DERBYNET_IMAGE:-ghcr.io/porkchopexpress86/derbynet_server:latest}
    restart: unless-stopped
    # No ports: phones, kiosks and timers reach the server through Caddy, on
    # plain HTTP and HTTPS alike (D117).
    environment:
      <<: *postgres
      TZ: ${TZ:-America/New_York}
      # The host user's uid and gid, which the files on the data volume
      # belong to (D106).  On Linux, docs/install-docker.md writes both into
      # .env beside this file.  Left empty, the server takes the data
      # folder's owner, or its own default.
      DERBYNET_UID: ${DERBYNET_UID:-}
      DERBYNET_GID: ${DERBYNET_GID:-}
      # Only the containers of this file reach the server, so every client
      # is Caddy, and the server believes the scheme, address and port
      # Caddy passes on (D117, D118).
      DERBYNET_TRUSTED_PROXIES: 0.0.0.0/0,::/0
      # The entrypoint uses Postgres on this host when the postgres service
      # below runs, as it does under `--profile postgres`.  Otherwise the
      # server keeps SQLite, one file on the data volume in WAL mode.  A
      # `docker compose exec -u derbynet server python manage.py ...` uses
      # the same database as the server (#308).
      DERBYNET_POSTGRES_HOST: postgres
    volumes:
      - data:/var/lib/derbynet
    depends_on:
      postgres:
        condition: service_healthy
        # Without the profile there is no postgres service to wait for.
        required: false

  # Plain HTTP on port 80, never redirected, and HTTPS on 443, both passed to
  # the server (D107, D117).  The start script below picks the certificate.
  caddy:
    image: caddy:2.11.9@sha256:4e2425f6009577e7c651010f16679c28f7d12df3e7b1914f941a7a3370746b3c
    restart: unless-stopped
    ports:
      - "${DERBYNET_HTTP_PORT:-80}:80"
      - "${DERBYNET_HTTPS_PORT:-443}:443"
    environment:
      # This computer's address on the race's network, such as 192.168.1.20,
      # which docs/install-docker.md writes into .env (D116).  Several are
      # separated by commas.
      DERBYNET_ADDRESS: ${DERBYNET_ADDRESS:-}
      # A domain name that reaches this computer from the internet, for a
      # Let's Encrypt certificate (D119).
      DERBYNET_DOMAIN: ${DERBYNET_DOMAIN:-}
      # Another ACME directory than Let's Encrypt's (D121).
      DERBYNET_ACME_CA: ${DERBYNET_ACME_CA:-}
    volumes:
      # Caddy's certificates and its own certificate authority.
      - caddy:/data
      # The server's volume, read-only, for the tls/ folder an image from
      # before D107 made (D119).
      - data:/derbynet:ro
    configs:
      - source: caddy-start
        target: /etc/caddy/derbynet-start.sh
    entrypoint: ["sh", "/etc/caddy/derbynet-start.sh"]

  postgres:
    image: postgres:17@sha256:846a7ddaa67a4b354ae1d21087b683901aa6113a4ed626d3787cfbf6c85e3e07
    profiles: [postgres]
    restart: unless-stopped
    environment: *postgres
    # No ports: only the server reaches it, on the compose network.
    volumes:
      - postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\""]
      interval: 2s
      timeout: 5s
      retries: 30

configs:
  # Caddy's start: it writes the Caddyfile and runs Caddy.  The certificate
  # for HTTPS is, in this order (D119):
  # - with DERBYNET_DOMAIN, one from Let's Encrypt, or DERBYNET_ACME_CA;
  # - with tls/cert.pem and key.pem on the server's volume, those two, as
  #   they are, for every name and address;
  # - otherwise one from Caddy's own certificate authority, for localhost,
  #   127.0.0.1 and each DERBYNET_ADDRESS; the first address is also the
  #   one a phone gets when it names none, as it does for an IP address
  #   (D116); any other name or address gets one when first asked for
  #   (on_demand), which a browser warns about until trusted.
  # /derbynet-ca.crt serves that authority's root, for phones to install.
  # The $$ are Compose's escape for a $ the script itself reads.
  caddy-start:
    content: |
      set -eu
      say() { echo "derbynet-caddy: $$*" >&2; }
      for name in $$(echo "$$DERBYNET_DOMAIN $$DERBYNET_ADDRESS" | tr , ' '); do
        case "$$name" in
          *[!A-Za-z0-9.:-]*)
            say "DERBYNET_DOMAIN and DERBYNET_ADDRESS hold names and addresses, not '$$name'"
            exit 1 ;;
        esac
      done
      case "$$DERBYNET_ACME_CA" in
        *[!A-Za-z0-9.:/_-]*)
          say "DERBYNET_ACME_CA is a URL, not '$$DERBYNET_ACME_CA'"
          exit 1 ;;
      esac
      addresses=$$(echo "$$DERBYNET_ADDRESS" | tr , ' ')
      set -- $$addresses
      first=$${1:-}
      tls=/derbynet/tls
      fallback=""
      if [ -n "$$DERBYNET_DOMAIN" ]; then
        say "a certificate for $$DERBYNET_DOMAIN from $${DERBYNET_ACME_CA:-Let's Encrypt}"
        sites="https://$$DERBYNET_DOMAIN"
        tls_line=""
      elif [ -f "$$tls/cert.pem" ] && [ -f "$$tls/key.pem" ]; then
        say "serving tls/cert.pem and tls/key.pem from the server's volume"
        sites="https://"
        tls_line="tls $$tls/cert.pem $$tls/key.pem"
      else
        for half in cert.pem key.pem; do
          if [ -e "$$tls/$$half" ]; then
            say "tls/ holds $$half alone; using Caddy's own certificate authority"
          fi
        done
        sites="https://localhost, https://127.0.0.1"
        for address in $$addresses; do sites="$$sites, https://$$address"; done
        say "certificates from Caddy's own authority for $$sites"
        tls_line="tls internal"
        fallback="https:// {
        tls internal {
          on_demand
        }
        import derbynet
      }"
      fi
      cat > /config/Caddyfile <<END
      {
        auto_https disable_redirects
        skip_install_trust
        $${first:+default_sni $$first}
        $${DERBYNET_ACME_CA:+acme_ca $$DERBYNET_ACME_CA}
      }
      (derbynet) {
        handle /derbynet-ca.crt {
          root * /data/caddy/pki/authorities/local
          rewrite * /root.crt
          header Content-Type application/x-x509-ca-cert
          file_server
        }
        handle {
          reverse_proxy server:80 {
            header_up X-Real-Port {remote_port}
          }
        }
      }
      http:// {
        import derbynet
      }
      $$sites {
        $$tls_line
        import derbynet
      }
      $$fallback
      END
      caddy fmt --overwrite /config/Caddyfile
      exec caddy run --config /config/Caddyfile --adapter caddyfile

volumes:
  data:
  postgres:
  caddy:
```

# Developing locally

The server is the Django port in `server/`; `server/README.md` covers its
tests and tools. Docker is the only install (D3), and you can develop against
the same image a race runs, with your checkout's `server/` mounted into it.
You need Docker (Docker Desktop on a Mac or Windows) and nothing else.

1. From the root of the cloned repository, build the image and start it with
   the `image` profile of `server/compose.yaml`:

   ```sh
   docker compose -f server/compose.yaml --profile image up --build image
   ```

   It serves `http://localhost:8081/` (`DERBYNET_IMAGE_HTTP_PORT` changes
   the port), plain HTTP only, as the image serves (D107). A browser on this
   computer treats `localhost` as a secure context, so the camera pages and
   the browser timer work there without HTTPS. The first visit sets up an
   event.

2. Edit the code. `server/` is mounted read-only over the image's copy, so
   gunicorn restarts its workers after a Python or template edit
   (`GUNICORN_RELOAD=1`), and the next page load shows it. Static files
   (CSS and JavaScript) are collected when the image is built, so rerun the
   command above with `--build` after changing them, and after a change to
   `server/uv.lock`.

3. Your data stays in the `image-data` volume between runs, as a race's does
   in its data volume. `docker compose -f server/compose.yaml --profile image
   down -v` deletes it.

The same `server/compose.yaml` also runs the PHP server and Django's
`runserver` side by side (`server/README.md`, "Run both servers").
`server/docker/README.md` describes the image itself: its settings, the
Caddy container that serves HTTPS in front of it, Postgres, and backups.

The guides for race crews and developers are Markdown files in `docs/`, starting
with `docs/install-docker.md` and `docs/developer-guide.md`. The server shows
them under `/docs/`, rendered when the image is built; with `runserver`, run
`uv run python manage.py build_docs` in `server/` first, and again after
changing a guide.
