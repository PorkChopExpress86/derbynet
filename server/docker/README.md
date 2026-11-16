# The DerbyNet server image

`derbynet_server` runs the Django server for a race (#58). Docker is the only way DerbyNet is installed (D3, ADR-14), so a laptop at the race and a hosted server run this same image. It is built for `linux/amd64` and `linux/arm64`; the arm64 image is for Apple Silicon Macs and arm64 servers, not for running the server on a Raspberry Pi (D21; #59 covers the Pi's timer and photo stand images).

Inside the container, nginx answers on port 80, plain HTTP only, and passes requests to gunicorn, which runs Django as the unprivileged `derbynet` user. HTTPS comes from a proxy in front of the image: `compose.yaml` runs Caddy for it (D107). On Linux, `derbynet` takes the host user's uid and gid (D106), so the volume's files belong to that user. Everything the server keeps is on one volume, `/var/lib/derbynet`: the SQLite database, photos, videos and the secret key. Caddy keeps its certificate authority and certificates on a second volume.

Release tags are published to `ghcr.io/porkchopexpress86/derbynet_server` (D89). To build the image from a checkout, from the repository root:

```sh
docker buildx build -f server/docker/Dockerfile -t derbynet_server .
```

## Start it

With `compose.yaml` from this folder, which runs the server and Caddy, after writing the host user's ids and this computer's address into `.env` on Linux:

```sh
echo "DERBYNET_UID=$(id -u)" >> .env
echo "DERBYNET_GID=$(id -g)" >> .env
echo "DERBYNET_ADDRESS=$(hostname -I | cut -d' ' -f1)" >> .env
docker compose up -d
```

On a Mac or Windows, `.env` needs the `DERBYNET_ADDRESS` line too, with the address `ipconfig getifaddr en0` or `ipconfig` prints (`docs/install-docker.md`, D116). The guide leaves the two ids out there (D106), where Docker Desktop runs the containers in a Linux virtual machine of its own ([Docker Desktop on Mac](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/); on Windows, its own `docker-desktop` WSL distribution, [Docker Desktop with WSL 2](https://docs.docker.com/desktop/features/wsl/)). Then open `http://<this computer's address>/` from any device on the network, or `https://` once the device trusts Caddy's certificate authority (`docs/secure-contexts.md`). The first visit sets up the first event.

Or the server alone, on plain HTTP, with one command:

```sh
docker run -d --name derbynet --restart unless-stopped \
  -p 80:80 -v derbynet:/var/lib/derbynet \
  -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) \
  ghcr.io/porkchopexpress86/derbynet_server
```

The pages that need a secure context (the browser timer, replay, the replay camera and check-in's live camera) then work only at `http://localhost/`, on this computer.

### Postgres instead of SQLite

SQLite, one file on the data volume in WAL mode, is the default and suits a race (ADR-05). To keep the race in Postgres instead, start compose with its `postgres` profile, which adds a Postgres container with its own volume:

```sh
docker compose --profile postgres up -d
```

Nothing else changes: the server finds the `postgres` service and uses it (`DERBYNET_POSTGRES_HOST` in `compose.yaml`). Pass `--profile postgres` to every later `docker compose` command for this install too. Choose the profile when you first start an install: a data volume keeps the database engine it first ran on, and refuses to start on the other one rather than open an empty event and leave the race in the first. To start again on the other engine, stop the server and delete `database-engine` on the volume: deleted under a running server, the next gunicorn worker to start would use SQLite. A command run with `docker compose exec -u derbynet server python manage.py ...` uses the server's database too: `settings.py` builds the `postgres` service's URL from `DERBYNET_POSTGRES_HOST`, the `POSTGRES_*` variables and the `database-engine` the entrypoint wrote at start, so no `DATABASE_URL` is needed, and with Postgres stopped such a command prints one message naming the host rather than make a SQLite file (#308). `DERBYNET_DEBUG=1` shows the Postgres password on Django's error page, so never set it at a race. Set `POSTGRES_PASSWORD` before the first start to choose the database password; any character works, and the Postgres container has no published port either way. Any other database is set with `DATABASE_URL`.

## What it serves

- **Plain HTTP on 80**, with no redirect to HTTPS. Kiosks, `derby-timer.jar` and the Pi scripts use `http://`. Under `compose.yaml`, Caddy publishes port 80 and passes it on, and the server publishes no port (D117).
- **HTTPS on 443**, under `compose.yaml`, from Caddy, for phone cameras, which browsers open only on a secure page (D107). The image itself has no TLS. Caddy's start script, in `compose.yaml`, picks the certificate (D119): with `DERBYNET_DOMAIN`, one from Let's Encrypt (or `DERBYNET_ACME_CA`); with `tls/cert.pem` and `tls/key.pem` on the data volume, as an image from before D107 made them, those, as they are; otherwise one from Caddy's own certificate authority for `localhost`, `127.0.0.1` and each `DERBYNET_ADDRESS`, the first of which is also served to a client that sends no server name, as a phone opening an IP address does (D116). With only one of the two files in `tls/`, Caddy uses its authority, and logs one line naming the missing file. Any other name gets one on demand, with a warning until trusted, and Caddy logs one "no protections in place" warning for that at each start. Caddy serves the authority's root at `/derbynet-ca.crt`. `checks.sh caddy`, `old-cert` and `acme` check each case. The server has no websocket endpoint yet (`racing/services/replay.py` renders the replay page's websocket URL empty), so there is nothing for Caddy to carry; its `reverse_proxy` passes websockets by default (D121).
- The legacy routes (`action.php` and the other PHP paths) also answer under `/derbynet/`, as the Debian install served them, so `derby-timer.jar` works at `http://<host>/` and `http://<host>/derbynet/`.

## Settings

The image sets these. `server/README.md`'s Settings table lists the rest, such as `DERBYNET_DEBUG` and the per-kind upload caps.

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_UID`, `DERBYNET_GID` | unset | The uid and gid the `derbynet` user runs gunicorn, migrate and a `docker exec -u derbynet` as, and the volume's files belong to (D106). Set both or neither. Unset, `derbynet` takes the data folder's owner when that is not root, or else the image's own ids (`/opt/derbynet/derbynet-ids`). 0 is refused, and an id another account in the image holds, such as `www-data`'s 33, is shared. When the folder's owner differs, the start gives every file to the new ids, once, and logs one line naming the old and new uids. An old volume's `tls/` goes to them too, still mode 0600 for `key.pem`, and Caddy reads it as root through a read-only mount (D119) |
| `TZ` | `America/New_York` | The race's time zone, which Django reads for `TIME_ZONE`. Set it with `-e TZ=...`: an `/etc/localtime` mount does not exist on Windows, and the server would fall back to UTC |
| `MAX_UPLOAD_SIZE` | `100M` | The largest request nginx takes (`client_max_body_size`), and Django's cap on a request's form fields (`DATA_UPLOAD_MAX_MEMORY_SIZE`). An nginx size: `16M`, `512k`, `1G`. The default, 104857600 bytes, is above the largest upload Django takes, a 100000000-byte replay clip (`DERBYNET_VIDEO_UPLOAD_MAX_MB`); `checks.sh upload` sends the photo stand's 30 MB photo through it |
| `DERBYNET_REQUEST_TIMEOUT` | `30` | Seconds a request may take: gunicorn's `--timeout` and nginx's `proxy_read_timeout`. Raise it for very long schedules, such as `600` |
| `GUNICORN_WORKERS` | `4` | gunicorn's sync workers. The default is `tools/load-run.sh`'s until #37's race-day load run sets it |
| `DERBYNET_URL_PREFIX` | empty | Serves the server under a path, such as `/derbynet`, as well as at `/`. Django builds its links with the prefix (`FORCE_SCRIPT_NAME`) |
| `DERBYNET_ALLOWED_HOSTS` | `*` | Comma-separated host names the server answers to. Phones reach a LAN server by an address the image can't know, so it answers any; a server on the internet should name its host, because a client could otherwise set the host that absolute links, such as check-in QR codes, are built with. A list always keeps `localhost`, `127.0.0.1` and `[::1]`, which the HEALTHCHECK asks for (#61) |
| `DERBYNET_CSRF_TRUSTED_ORIGINS` | empty | Extra origins for the manager pages behind another HTTPS proxy |
| `DERBYNET_TRUSTED_PROXIES` | empty | Comma-separated addresses or networks of a proxy in front of the container (below). Only from these are a request's `X-Forwarded-Proto`, `X-Forwarded-For` (nginx's `set_real_ip_from`, the last address in it that is no trusted proxy's) and `X-Real-Port` believed; from anyone else, the scheme, address and port are the connection's (D118). `compose.yaml` sets `0.0.0.0/0,::/0`, since only Caddy reaches the server there (D117) |
| `DJANGO_SECRET_KEY` | unset | When unset, the server makes one on first start in `/var/lib/derbynet/secret_key`, mode 0600 |
| `DATABASE_URL` | unset | Another database. Unset, SQLite on the volume, or the compose `postgres` service when the volume runs on it, for the server and for a `docker compose exec`'d `manage.py` alike |
| `DERBYNET_DATA_DIR`, `DERBYNET_CONFIG_DIR` | `/var/lib/derbynet` | Where the data and the install's files are. Keep both on the volume |
| `DERBYNET_DOCS_ROOT` | `/opt/derbynet/docs` | The guides under `/docs/`, rendered from `docs/` when the image is built (#61). Leave it |
| `GUNICORN_RELOAD` | `0` | `1` restarts gunicorn's workers when a Python file or a template changes, for development (README.md) |

## Timers on a Mac or Windows laptop

Docker Desktop on a Mac cannot pass a USB serial device into a container (D3). On a Mac or Windows laptop, connect the timer through the browser timer instead: open `https://<host>/timer.php` in Chrome or Edge on the laptop the timer is plugged into, which talks to the timer over Web Serial. The image serves that page. Web Serial needs a secure page, so use the HTTPS address and accept the certificate; plain `http://` works only as `http://localhost/timer.php` on the computer running the container. Over plain HTTP to another address, Chrome hides Web Serial and the page sends the browser to `/timer/no-serial/`. On a Linux host, `derby-timer.jar` runs on the host and connects to `http://<host>/`. `docs/install-docker.md` says the same for race crews.

## Behind another web server

To serve DerbyNet under a path of an existing web site, publish the container on a local port and proxy that path to it.

**A difference from the PHP image:** set `DERBYNET_URL_PREFIX` to the same path. The PHP image worked under any proxied path with no setting, because PHP's pages used relative links; Django builds absolute links, so it must know the path. Set `DERBYNET_TRUSTED_PROXIES` to the address the proxy reaches the container from, so the container believes the proxy's `X-Forwarded-Proto`; on Docker's default bridge network that is its gateway, `172.17.0.1` (`docker network inspect bridge` shows it). For example, for `/my-derbynet`:

```sh
docker run -d --name derbynet --restart unless-stopped -p 127.0.0.1:8000:80 \
  -v derbynet:/var/lib/derbynet -e DERBYNET_URL_PREFIX=/my-derbynet \
  -e DERBYNET_TRUSTED_PROXIES=172.17.0.1 -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) \
  ghcr.io/porkchopexpress86/derbynet_server
```

```nginx
location /my-derbynet/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 100M;
}
```

`Host` keeps absolute links, such as QR codes, on the site's own name, `X-Forwarded-Proto` tells the server when the site was reached over HTTPS, and `X-Forwarded-For` gives it the client's address, for the kiosks and ballots (D118). `docs/install-docker.md`'s "Use your own proxy" names the settings for Nginx Proxy Manager and Traefik too, untested (D107). `.github/workflows/image.yml` runs the shell suite through this `location` block, read from this file.

## Your data

### Volumes and bind mounts

Use a named volume, as above. A bind mount of a host folder works too. With no `DERBYNET_UID`, `derbynet` takes the folder's owner when that is not root, so a folder the host user made stays that user's (D106). Each start gives `derbynet` any file under the folder that another owner holds, and a start whose ids match changes no file. When it can't (a network share that maps root to nobody, say), it stops with a message naming the folder and the `chown` to run on the host.

Run one container per volume. Each start applies the database migrations, and two containers on one volume could apply them at once.

### Back up

A full backup is a copy of the data volume and Caddy's volume, and also the Postgres volume under the `postgres` profile, taken with the server stopped (D65). Caddy's volume holds its certificate authority, so phones keep trusting it after a restore (D120). The backup holds the install's secret key and the authority's private key, so keep the backup files private: `tar` writes to its output, the shell saves it, and `umask 077` makes the file readable by you alone. From this folder, with compose (name your volumes `derbynet_data`, `derbynet_caddy` and `derbynet_postgres`, as compose does):

```sh
umask 077
docker compose stop
docker run --rm -v derbynet_data:/data:ro --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-data.tgz
docker run --rm -v derbynet_caddy:/data:ro --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-caddy.tgz
docker compose start
```

Under the `postgres` profile, run each `docker compose` command with `--profile postgres`, and after the first `tar` also copy the Postgres volume:

```sh
docker run --rm -v derbynet_postgres:/data:ro --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-postgres.tgz
```

On Windows, run these in WSL as written. PowerShell 7.4 or later, which keeps a command's output byte for byte, runs them with three changes: each command on one line, since PowerShell continues a line with a backtick, not `\`; no `umask 077`, which PowerShell lacks, so save the files in your own user folder, not a shared one; and the restore below reads its file from a mounted folder, since PowerShell has no `<`.

The backup holds the whole race: the database, photos, car photos, videos, the secret key and Caddy's certificate authority. A snapshot from the settings page leaves out videos and some race state (D65).

### Restore

Into fresh volumes, on this computer or another one, from the folder holding the backup files. `down --volumes` deletes the volumes this install has now.

```sh
docker compose down --volumes
docker compose create
docker run --rm -i -v derbynet_data:/data --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-data.tgz
docker run --rm -i -v derbynet_caddy:/data --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-caddy.tgz
docker compose up -d
```

Under the `postgres` profile, pass `--profile postgres` to each `docker compose` command, and before `up` also restore the Postgres volume:

```sh
docker run --rm -i -v derbynet_postgres:/data --entrypoint tar \
  ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-postgres.tgz
```

In PowerShell, each restore reads the backup from the folder that holds it:

```powershell
docker run --rm -v derbynet_data:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-data.tgz
docker run --rm -v derbynet_caddy:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-caddy.tgz
docker run --rm -v derbynet_postgres:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-postgres.tgz
```

`.github/workflows/image.yml` runs these steps, with SQLite and with Postgres, on an event holding a racer photo, a car photo and a video, and checks that Caddy's root is the same after the restore (`server/docker/ci/checks.sh backup`).

### Upgrade

Pull the new image and start a new container on the same volume (`docker compose pull && docker compose up -d`). The new container applies its migrations at start. An install from before D107 keeps serving its `tls/` certificate through Caddy; `docs/install-docker.md` says how to move a `docker run` install to Compose, and how to move to Caddy's certificate authority.

## Logs

`docker compose logs` shows nginx's request log, gunicorn's log and Caddy's. Request bodies are never logged, so a role login's password never reaches the log (#64 S36).
