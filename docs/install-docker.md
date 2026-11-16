# Install DerbyNet with Docker

DerbyNet runs as one Docker container. The container is the only way to install DerbyNet (D3). A laptop at the race and a server on the internet run the same image.

This guide covers:

- a laptop or desktop with Linux, macOS, or Windows, on the race's own network;
- a hosted server on the internet, behind your web site's own web server.

## What you need

- A computer with a 64-bit Intel or AMD processor, or an Apple Silicon Mac or other arm64 computer. A Raspberry Pi is not supported as the server (D21). A Pi can still be a kiosk, a timer client, or a photo stand (see [the photo stand guide](photo-stand.md)).
- Docker:
  - On a Mac or a Windows computer, install Docker Desktop from <https://www.docker.com/products/docker-desktop/>. On Windows, keep the default WSL 2 engine. You need no Docker account.
  - On Linux, install Docker Engine for your distribution from <https://docs.docker.com/engine/install/>.
- A network that the phones, tablets and kiosk screens at the race can join. The race needs no internet. These guides are part of the server, so they work offline too.

## Start DerbyNet

Open a terminal: Terminal on a Mac, PowerShell on Windows, or a shell on Linux. Then run this one command, with your own time zone.

On Linux:

```sh
docker run -d --name derbynet --restart unless-stopped -p 80:80 -p 443:443 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

On a Mac or Windows:

```sh
docker run -d --name derbynet --restart unless-stopped -p 80:80 -p 443:443 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago ghcr.io/porkchopexpress86/derbynet_server
```

What each part does:

| Part | Meaning |
|---|---|
| `-d --name derbynet` | Runs the container in the background, named `derbynet`. |
| `--restart unless-stopped` | Starts it again after a reboot, until you stop it. |
| `-p 80:80 -p 443:443` | Serves plain HTTP on port 80 and HTTPS on port 443 of this computer. |
| `-v derbynet:/var/lib/derbynet` | Keeps everything in a Docker volume named `derbynet`: the database, photos, videos, and the server's keys. |
| `-e TZ=America/Chicago` | The race's time zone. Use a name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>. |
| `-e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g)` | On Linux only. The server runs as your own user and group, so the files it keeps belong to you, in a volume and in a folder of yours alike (D106). The guide leaves them out on a Mac or Windows, where Docker Desktop runs the containers in a Linux virtual machine of its own: on a Mac, "the Docker daemon and containers run in a lightweight Linux VM managed by Docker" ([Docker Desktop on Mac](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/)), and on Windows, "Docker Desktop runs inside its own `docker-desktop` WSL distribution" ([Docker Desktop with WSL 2](https://docs.docker.com/desktop/features/wsl/)). |

Docker downloads the image the first time, which takes a minute or two. On Linux, put `sudo` in front of each `docker` command unless your user is in the `docker` group. Run the command from your own user's shell, not a root shell: the shell fills in `$(id -u)` before `sudo` runs, so it is still your own id, but in a root shell it is 0, and the server refuses to run as root.

The later `docker run` commands in this guide are the Linux ones. On a Mac or Windows, leave out `-e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g)`.

The image is published when the project tags a release (D89). The commands here use the image's `latest` tag, which only a full release sets. Until the first one, only test releases are published, so add the newest test release's tag from the project's [Releases page](https://github.com/PorkChopExpress86/derbynet/releases) to the end of the image name in each command, after a colon, for example `:v0.1.0-rc2`. Use `v0.1.0-rc2` or later, since `v0.1.0-rc1` cannot run the browser timer. If the command says the image is not found, build it from a copy of the repository instead. From the repository's top folder, run `docker buildx build -f server/docker/Dockerfile -t derbynet_server .` and use `derbynet_server` in place of the image name.

### If port 80 or 443 is taken

Another web server on the computer may already use port 80 or 443. Docker then refuses to start the container and names the port. Either stop the other server, or publish DerbyNet on other ports, such as `-p 8080:80 -p 8443:443`. Then add the port to every address in this guide, as in `http://localhost:8080/`.

### With Docker Compose

The repository holds the same setup as a Compose file, `server/docker/compose.yaml`. Save that file in a folder of its own. On Linux, first write your user's ids into a file named `.env` beside it, from that folder, so the server runs as you (D106):

```sh
echo "DERBYNET_UID=$(id -u)" >> .env
echo "DERBYNET_GID=$(id -g)" >> .env
```

On a Mac or Windows, skip that step, as the guide leaves the ids out there. Then run this from the folder:

```sh
docker compose up -d
```

Like `docker run`, Compose pulls the published image's `latest` tag. Until the first full release, put a line naming a test release in a file named `.env` beside `compose.yaml`, for example `DERBYNET_IMAGE=ghcr.io/porkchopexpress86/derbynet_server:v0.1.0-rc2`. If no image is published yet, it fails the same way. Then build the image from the repository instead: from the folder that holds `compose.yaml` in a copy of the repository, run `docker compose up -d --build`.

Compose names the volume `derbynet_data`. The file reads `TZ` from your shell, or from a file named `.env` beside it, so a line `TZ=America/Chicago` in `.env` sets the time zone. The Postgres profile below also needs Compose.

## Open DerbyNet

On the computer running the container, open <http://localhost/> in a browser.

The first visit opens Set-Up, which creates the first event. After that, the server asks for a login. A new install has default roles and passwords, and every page warns a logged-in user until you change them. See [Roles and passwords](roles-and-passwords.md).

Phones and other computers open the server by this computer's address on the network:

- On a Mac: System Settings, then Network, then the connection in use. Or run `ipconfig getifaddr en0`.
- On Windows: run `ipconfig` and read the IPv4 address of the connection in use.
- On Linux: run `hostname -I`.

With the address `192.168.1.20`, phones open `http://192.168.1.20/`. If a phone cannot reach the server, check that the computer's firewall lets in ports 80 and 443. If Windows asks whether to let Docker Desktop through its firewall on private networks, allow it.

The browser timer, the replay page, and the live camera at check-in need the HTTPS address, such as `https://192.168.1.20/`. Each phone warns about the certificate once. See [Secure contexts](secure-contexts.md) for why, and how to accept it on iPhone and Android.

## Set the time zone

The server takes its time zone only from the `TZ` variable, or from `DERBYNET_TIME_ZONE` when that is set (D56). Set-Up and the settings page show the zone in use, and say how to change it. Without either variable, the image uses `America/New_York`.

Pass the zone with `-e TZ=...` as above. Do not mount the host's `/etc/localtime` into the container: that file does not exist on Windows, and the server never reads it, so the zone would stay `America/New_York`.

To change the zone later, remove the container and start it again with the new `-e TZ=...`. Your data stays in the volume:

```sh
docker rm -f derbynet
docker run -d --name derbynet --restart unless-stopped -p 80:80 -p 443:443 -v derbynet:/var/lib/derbynet -e TZ=America/Denver -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

## Your data

Everything the server keeps is in the data volume, mounted at `/var/lib/derbynet`:

- the database, as one SQLite file, unless you chose Postgres;
- racer photos, car photos, and replay videos, in a folder per event;
- the server's secret key, and its HTTPS certificate and key in the `tls` folder.

Removing the container keeps the volume. `docker volume rm derbynet` deletes it, with every event in it.

Run one container per volume. Each start applies the database migrations, and two containers on one volume could apply them at once.

You can use a folder on the host instead of a named volume, such as `-v /srv/derbynet:/var/lib/derbynet` on Linux. The server runs as the user that `DERBYNET_UID` and `DERBYNET_GID` name, and without them as the folder's owner, unless that is root. Either way the folder's files belong to that user, so you can read and copy them on the host. When the container cannot give a file to that user, as on a network share, it stops with a message naming the folder and the `chown` command to run on the host.

When the files belong to another user, as after an upgrade from an older image or with a new `DERBYNET_UID`, the next start gives them all to the new user, once. Its log says so in one line. On a large photo folder that start takes longer.

## Connect the timer

A track timer plugs into a computer by USB or a serial cable. Two programs can talk to it and report each heat to the server:

- The browser timer, a page of the server that talks to the timer through Chrome or Edge (Web Serial). It is the supported timer client (D3). Open it on the computer the timer is plugged into, and log in as RaceCoordinator (D53).
- `derby-timer.jar`, the Java program from earlier versions of DerbyNet. It keeps working with this server.

Web Serial works only on a secure page. Open the browser timer at the HTTPS address, such as `https://192.168.1.20/timer.php`, and accept the certificate. On the computer that runs the container, `http://localhost/timer.php` works too. A plain `http://` address of another computer does not: there the browser hides Web Serial and the page says to use Chrome or Edge.

On a Mac or Windows laptop, use the browser timer. Docker Desktop cannot pass a USB serial device into a container (D3), and the server needs none: the browser on the laptop talks to the timer, and sends the results to the server.

On a Linux server, either client works. `derby-timer.jar` runs on the host, outside the container, and connects to `http://localhost/`.

## HTTPS and the certificate

On its first start, the container makes a private key and a certificate for this install in the `tls` folder of the data volume. Every install gets its own key. The certificate lasts 825 days, and the container makes a new one when it has expired.

Browsers do not know this certificate, so each phone warns once (see [Secure contexts](secure-contexts.md)). To use a certificate of your own, put it as `cert.pem`, with its key as `key.pem`, in the `tls` folder before you start the container.

Plain HTTP on port 80 never redirects to HTTPS. Kiosks, `derby-timer.jar`, and the Raspberry Pi scripts use `http://`.

## Use Postgres instead of SQLite

SQLite, one file on the data volume, is the default and suits a race (D1). To keep the race in Postgres instead, start Compose with its `postgres` profile, which adds a Postgres container with its own volume:

```sh
docker compose --profile postgres up -d
```

Pass `--profile postgres` to every later `docker compose` command for this install too. Choose the profile when you first start an install. A `docker compose exec -u derbynet server python manage.py ...` command uses the same database as the server. The `-u derbynet` runs it as the server's own user: without it the command runs as root and stops with a message, since an import run as root leaves the event's photos readable only by root (D103). See [the database guide](database.md) for the details.

## Settings

The container reads these variables. Pass each with `-e NAME=value` on `docker run`, or set it under `environment` in the Compose file.

| Variable | Default | Effect |
|---|---|---|
| `TZ` | `America/New_York` | The race's time zone. |
| `DERBYNET_UID`, `DERBYNET_GID` | unset | The user and group ids the server runs as, which own its files. On Linux, set both, to `$(id -u)` and `$(id -g)`. Unset, the server takes the owner of a folder you mount, or else its own default. Root's id, 0, is refused. |
| `DERBYNET_TIME_ZONE` | unset | A time zone that wins over `TZ`. |
| `MAX_UPLOAD_SIZE` | `100M` | The largest request the server takes, such as a replay clip. A size such as `16M`, `512k` or `1G`. |
| `DERBYNET_REQUEST_TIMEOUT` | `30` | Seconds a request may take. Raise it for a very long schedule, such as `600`. |
| `GUNICORN_WORKERS` | `4` | How many requests the server handles at once. |
| `DERBYNET_URL_PREFIX` | empty | Serves the server under a path, such as `/derbynet`, as well as at `/`. See the reverse proxy section. |
| `DERBYNET_ALLOWED_HOSTS` | `*` | The host names the server answers to, separated by commas. A server on the internet should name its own. `localhost` and `127.0.0.1` always answer, for the container's health check. |
| `DERBYNET_CSRF_TRUSTED_ORIGINS` | empty | Extra origins, such as `https://derby.example.org`, for the pages behind another HTTPS proxy. |
| `DERBYNET_TRUSTED_PROXIES` | empty | Addresses or networks of a proxy in front of the container, separated by commas. |
| `DERBYNET_LOCK_SETTINGS` | unset | `1` locks the photo and video folder settings, and hides the event controls, for a shared server. |
| `DATABASE_URL` | unset | Another database. See [the database guide](database.md). |
| `POSTGRES_PASSWORD` | `derbynet` | The Postgres password under the `postgres` profile. Set it before the first start. |
| `DJANGO_SECRET_KEY` | unset | The server's secret key. Unset, the server makes one on the volume on its first start. |

## Host DerbyNet on a web server

To add DerbyNet to an existing web site, run the container on a local port, and have the web server pass a path to it. Set `DERBYNET_URL_PREFIX` to that same path, because the server builds its links with it. Set `DERBYNET_TRUSTED_PROXIES` to the address the web server reaches the container from, so the server believes the web server when it says a request came over HTTPS. On Docker's default network that address is `172.17.0.1`. `docker network inspect bridge` shows it.

For DerbyNet at `/my-derbynet` of your site:

```sh
docker run -d --name derbynet --restart unless-stopped -p 127.0.0.1:8000:80 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) -e DERBYNET_URL_PREFIX=/my-derbynet -e DERBYNET_TRUSTED_PROXIES=172.17.0.1 -e DERBYNET_ALLOWED_HOSTS=derby.example.org ghcr.io/porkchopexpress86/derbynet_server
```

In nginx:

```nginx
location /my-derbynet/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 100M;
}
```

`Host` keeps links, such as check-in QR codes, on the site's own name. `X-Forwarded-Proto` tells the server when the site was reached over HTTPS. Your site's own certificate then covers the phones, and nobody sees a certificate warning.

The legacy paths that `derby-timer.jar` and the kiosks use, such as `action.php`, also answer under `/derbynet/`, as older installs served them.

## Back up and restore

A full backup is a copy of the data volume, taken with the server stopped (D65). It holds the whole race: the database, photos, videos, and the server's keys. Keep the backup file private, since it holds the HTTPS private key.

With the `docker run` setup above, on Linux or a Mac:

```sh
umask 077
docker stop derbynet
docker run --rm -v derbynet:/data:ro --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-data.tgz
docker start derbynet
```

To restore into a new volume, on this computer or another one:

```sh
docker rm -f derbynet
docker volume rm derbynet
docker volume create derbynet
docker run --rm -i -v derbynet:/data --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-data.tgz
```

Then start the container as in [Start DerbyNet](#start-derbynet).

On Windows, run these in WSL as written. PowerShell needs two changes, and only version 7.4 or later keeps a command's output byte for byte, which the backup needs:

- Leave out `umask 077`, which PowerShell does not have. Save the backup in your own user folder, not a shared one.
- PowerShell has no `<`. Restore from the folder that holds `derbynet-data.tgz`, which the container reads directly:

```powershell
docker run --rm -v derbynet:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-data.tgz
```

With Compose, the volume is `derbynet_data`, and under the `postgres` profile the Postgres volume `derbynet_postgres` needs the same copy. `server/docker/README.md` has the Compose commands.

A snapshot from Set-Up is not a full backup. It leaves out videos and some race state (D65).

## Upgrade

Pull the new image, and start a new container on the same volume:

```sh
docker pull ghcr.io/porkchopexpress86/derbynet_server
docker rm -f derbynet
docker run -d --name derbynet --restart unless-stopped -p 80:80 -p 443:443 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

With Compose: `docker compose pull`, then `docker compose up -d`. On Linux, add the `.env` lines from [With Docker Compose](#with-docker-compose) first, if your `.env` has none. The new container updates the database when it starts. Take a backup first.

An install from before the server ran as your user keeps its files under the image's own user. The first start with `DERBYNET_UID` gives them to you, once, and logs one line naming both users.

## Stop, start, and logs

- `docker stop derbynet` stops the server, and `docker start derbynet` starts it again.
- In Docker Desktop, the Containers list has the same buttons.
- `docker logs derbynet` shows the server's log. Request bodies are never logged, so no password reaches the log.
