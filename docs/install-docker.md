# Install DerbyNet with Docker

DerbyNet runs in Docker. The container is the only way to install DerbyNet (D3). A laptop at the race and a server on the internet run the same image.

The DerbyNet container serves plain HTTP. A second container, Caddy, serves HTTPS in front of it (D107), which the phone cameras and the browser timer need. Docker Compose starts the two together from one file.

This guide covers:

- a laptop or desktop with Linux, macOS, or Windows, on the race's own network;
- a hosted server on the internet, with its own domain name, or behind a web server you already run.

## What you need

- A computer with a 64-bit Intel or AMD processor, or an Apple Silicon Mac or other arm64 computer. A Raspberry Pi is not supported as the server (D21). A Pi can still be a kiosk, a timer client, or a photo stand (see [the photo stand guide](photo-stand.md)).
- Docker:
  - On a Mac or a Windows computer, install Docker Desktop from <https://www.docker.com/products/docker-desktop/>. On Windows, keep the default WSL 2 engine. You need no Docker account.
  - On Linux, install Docker Engine for your distribution from <https://docs.docker.com/engine/install/>, with its Compose plugin. Compose 2.23.1 or later reads the Compose file; `docker compose version` prints yours.
- A network that the phones, tablets and kiosk screens at the race can join. The race needs no internet. These guides are part of the server, so they work offline too.

## Start DerbyNet

Open a terminal: Terminal on a Mac, PowerShell on Windows, or a shell on Linux.

Make a folder for DerbyNet, and save the Compose file in it as `compose.yaml`. The file is `server/docker/compose.yaml` in the repository, and the project's `README.md` shows it whole. Then, from that folder, write this computer's settings into a file named `.env` beside it.

On Linux, run:

```sh
echo "DERBYNET_UID=$(id -u)" >> .env
echo "DERBYNET_GID=$(id -g)" >> .env
echo "DERBYNET_ADDRESS=$(hostname -I | cut -d' ' -f1)" >> .env
echo "TZ=America/Chicago" >> .env
```

On a Mac, run:

```sh
echo "DERBYNET_ADDRESS=$(ipconfig getifaddr en0)" >> .env
echo "TZ=America/Chicago" >> .env
```

On Windows, run `ipconfig`, and read the IPv4 address of the connection in use, such as `192.168.1.20`. Make the file `.env` in Notepad with these two lines, with your own address, and save it beside `compose.yaml`:

```
DERBYNET_ADDRESS=192.168.1.20
TZ=America/Chicago
```

Use your own time zone, a name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>. Then start DerbyNet:

```sh
docker compose up -d
```

What each line of `.env` does:

| Line | Meaning |
|---|---|
| `DERBYNET_UID`, `DERBYNET_GID` | On Linux only. The server runs as your own user and group, so the files it keeps belong to you (D106). The guide leaves them out on a Mac or Windows, where Docker Desktop runs the containers in a Linux virtual machine of its own: on a Mac, "the Docker daemon and containers run in a lightweight Linux VM managed by Docker" ([Docker Desktop on Mac](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/)), and on Windows, "Docker Desktop runs inside its own `docker-desktop` WSL distribution" ([Docker Desktop with WSL 2](https://docs.docker.com/desktop/features/wsl/)). |
| `DERBYNET_ADDRESS` | This computer's address on the race's network, which phones open. Caddy makes the HTTPS certificate for it (D116). `hostname -I` prints the addresses of a Linux computer, and the command keeps the first. When the computer has several, as with a VPN, put the one the phones reach. |
| `TZ` | The race's time zone. |

Compose starts two containers, `server` and `caddy`, and keeps their data in two volumes, `derbynet_data` and `derbynet_caddy`. Both restart after a reboot, until you stop them. Docker downloads the images the first time, which takes a minute or two. On Linux, put `sudo` in front of each `docker` command unless your user is in the `docker` group. Run the commands from your own user's shell, not a root shell: the shell fills in `$(id -u)` before `sudo` runs, so it is still your own id, but in a root shell it is 0, and the server refuses to run as root.

The Compose file uses the published image's `latest` tag, which only a full release sets (D89). Until the first one, only test releases are published, so add a line naming the newest test release from the project's [Releases page](https://github.com/PorkChopExpress86/derbynet/releases) to `.env`, for example `DERBYNET_IMAGE=ghcr.io/porkchopexpress86/derbynet_server:v0.1.0-rc3`. If the image is not found, build it from a copy of the repository instead: from the folder that holds `compose.yaml` in a copy of the repository, run `docker compose up -d --build`.

When the computer's address changes, as on another network, put the new one in `.env` and run `docker compose up -d` again. Until then, phones see a certificate warning for the new address, which they can accept.

### If port 80 or 443 is taken

Another web server on the computer may already use port 80 or 443. Docker then refuses to start Caddy and names the port. Either stop the other server, or publish DerbyNet on other ports, with lines such as `DERBYNET_HTTP_PORT=8080` and `DERBYNET_HTTPS_PORT=8443` in `.env`. Then add the port to every address in this guide, as in `http://localhost:8080/`.

### Plain HTTP with one command

Without Compose, one `docker run` starts the server alone, on plain HTTP. On Linux:

```sh
docker run -d --name derbynet --restart unless-stopped -p 80:80 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

On a Mac or Windows, leave out `-e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g)`. The data goes in a volume named `derbynet`.

This setup has no HTTPS. The browser timer, the replay page, the replay camera, and the live camera at check-in then work only at `http://localhost/`, in a browser on the computer that runs the container. Every other page works from any device. For HTTPS on phones, use the Compose setup above.

The later `docker run` commands in this guide are the Linux ones. On a Mac or Windows, leave out the two ids.

## Open DerbyNet

On the computer running the containers, open <http://localhost/> in a browser.

The first visit opens Set-Up, which creates the first event. After that, the server asks for a login. A new install has default roles and passwords, and every page warns a logged-in user until you change them. See [Roles and passwords](roles-and-passwords.md).

Phones and other computers open the server by the address in `.env`, such as `http://192.168.1.20/`. If a phone cannot reach the server, check that the computer's firewall lets in ports 80 and 443. If Windows asks whether to let Docker Desktop through its firewall on private networks, allow it.

The browser timer, the replay page, and the live camera at check-in need the HTTPS address, such as `https://192.168.1.20/`. Install Caddy's certificate authority on each phone once, from `http://192.168.1.20/derbynet-ca.crt`, and the phone then trusts it with no warning. [Secure contexts](secure-contexts.md) says how, on iPhone, Android and a laptop.

## Set the time zone

The server takes its time zone only from the `TZ` variable, or from `DERBYNET_TIME_ZONE` when that is set (D56). Set-Up and the settings page show the zone in use, and say how to change it. Without either variable, the image uses `America/New_York`.

Set the zone with the `TZ` line in `.env`, or `-e TZ=...` on `docker run`. Do not mount the host's `/etc/localtime` into the container: that file does not exist on Windows, and the server never reads it, so the zone would stay `America/New_York`.

To change the zone later, change the `TZ` line in `.env` and run `docker compose up -d`. With `docker run`, remove the container and start it again with the new `-e TZ=...`. Your data stays in the volume:

```sh
docker rm -f derbynet
docker run -d --name derbynet --restart unless-stopped -p 80:80 -v derbynet:/var/lib/derbynet -e TZ=America/Denver -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

## Your data

Everything the server keeps is in the data volume, mounted at `/var/lib/derbynet`:

- the database, as one SQLite file, unless you chose Postgres;
- racer photos, car photos, and replay videos, in a folder per event;
- the server's secret key.

Caddy's volume, `derbynet_caddy`, holds its certificate authority and the certificates it made.

Removing a container keeps its volume. `docker compose down` keeps the volumes too. `docker compose down --volumes`, or `docker volume rm derbynet` for the `docker run` setup, deletes them, with every event in them.

Run one server per volume. Each start applies the database migrations, and two servers on one volume could apply them at once.

You can use a folder on the host instead of a named volume, such as `-v /srv/derbynet:/var/lib/derbynet` on Linux. The server runs as the user that `DERBYNET_UID` and `DERBYNET_GID` name, and without them as the folder's owner, unless that is root. Either way the folder's files belong to that user, so you can read and copy them on the host. When the container cannot give a file to that user, as on a network share, it stops with a message naming the folder and the `chown` command to run on the host.

When the files belong to another user, as after an upgrade from an older image or with a new `DERBYNET_UID`, the next start gives them all to the new user, once. Its log says so in one line. On a large photo folder that start takes longer.

## Connect the timer

A track timer plugs into a computer by USB or a serial cable. Two programs can talk to it and report each heat to the server:

- The browser timer, a page of the server that talks to the timer through Chrome or Edge (Web Serial). It is the supported timer client (D3). Open it on the computer the timer is plugged into, and log in as RaceCoordinator (D53).
- `derby-timer.jar`, the Java program from earlier versions of DerbyNet. It keeps working with this server.

Web Serial works only on a secure page. Open the browser timer at the HTTPS address, such as `https://192.168.1.20/timer.php`, on a computer that trusts Caddy's certificate authority. On the computer that runs the containers, `http://localhost/timer.php` works too. A plain `http://` address of another computer does not: there the browser hides Web Serial and the page says to use Chrome or Edge.

On a Mac or Windows laptop, use the browser timer. Docker Desktop cannot pass a USB serial device into a container (D3), and the server needs none: the browser on the laptop talks to the timer, and sends the results to the server.

On a Linux server, either client works. `derby-timer.jar` runs on the host, outside the container, and connects to `http://localhost/`.

## HTTPS and the certificate

Caddy serves HTTPS on port 443, and plain HTTP on port 80, and passes both to the server (D107, D117). Plain HTTP never redirects to HTTPS: kiosks, `derby-timer.jar`, and the Raspberry Pi scripts use `http://`.

Caddy picks the certificate for HTTPS in this order (D119):

1. With a `DERBYNET_DOMAIN` line in `.env`, such as `DERBYNET_DOMAIN=derby.example.org`, a certificate from Let's Encrypt for that domain. The domain must reach this computer from the internet, on ports 80 and 443. Every phone trusts it with no step.
2. With `cert.pem` and `key.pem` in the `tls` folder of the data volume, as an install from before Caddy made them, those two, as they are, even once expired. Phones that accepted that certificate before keep it.
3. Otherwise a certificate from Caddy's own certificate authority, for `localhost`, `127.0.0.1` and each address in `DERBYNET_ADDRESS`. A phone that opens another name or address gets one too, with a warning it can accept. With only one of the two files in `tls`, Caddy uses its authority, and its log names the file that is missing, as in "tls/ has no key.pem beside cert.pem".

Caddy's certificate authority belongs to this install alone. Its root lasts years, and Caddy renews the certificates it signs by itself. Install the root once on each device, as [Secure contexts](secure-contexts.md) shows, and keep the `derbynet_caddy` volume: a new one makes a new authority, which every phone must install again.

`DERBYNET_ACME_CA` points Caddy at another ACME server than Let's Encrypt, such as Let's Encrypt's staging server, `https://acme-staging-v02.api.letsencrypt.org/directory`, while you try a domain out.

## Use Postgres instead of SQLite

SQLite, one file on the data volume, is the default and suits a race (D1). To keep the race in Postgres instead, start Compose with its `postgres` profile, which adds a Postgres container with its own volume:

```sh
docker compose --profile postgres up -d
```

Pass `--profile postgres` to every later `docker compose` command for this install too. Choose the profile when you first start an install. A `docker compose exec -u derbynet server python manage.py ...` command uses the same database as the server. The `-u derbynet` runs it as the server's own user: without it the command runs as root and stops with a message, since an import run as root leaves the event's photos readable only by root (D103). See [the database guide](database.md) for the details.

## Settings

The server reads these variables. With Compose, put each in `.env` when the Compose file names it, or under the server's `environment` in the file. With `docker run`, pass each with `-e NAME=value`.

| Variable | Default | Effect |
|---|---|---|
| `TZ` | `America/New_York` | The race's time zone. |
| `DERBYNET_UID`, `DERBYNET_GID` | unset | The user and group ids the server runs as, which own its files. On Linux, set both, to `$(id -u)` and `$(id -g)`. Unset, the server takes the owner of a folder you mount, or else its own default. Root's id, 0, is refused. |
| `DERBYNET_TIME_ZONE` | unset | A time zone that wins over `TZ`. |
| `MAX_UPLOAD_SIZE` | `100M` | The largest request the server takes, such as a replay clip. A size such as `16M`, `512k` or `1G`. |
| `DERBYNET_REQUEST_TIMEOUT` | `30` | Seconds a request may take. Raise it for a very long schedule, such as `600`. |
| `GUNICORN_WORKERS` | `4` | How many requests the server handles at once. |
| `DERBYNET_URL_PREFIX` | empty | Serves the server under a path, such as `/derbynet`, as well as at `/`. See [Use your own proxy](#use-your-own-proxy). |
| `DERBYNET_ALLOWED_HOSTS` | `*` | The host names the server answers to, separated by commas. A server on the internet should name its own. `localhost` and `127.0.0.1` always answer, for the container's health check. |
| `DERBYNET_CSRF_TRUSTED_ORIGINS` | empty | Extra origins, such as `https://derby.example.org`, for the pages behind another HTTPS proxy. |
| `DERBYNET_TRUSTED_PROXIES` | empty | Addresses or networks of a proxy in front of the server, separated by commas. The server believes only these when they say a request came over HTTPS, and from which client address. The Compose file sets it, since only Caddy reaches the server there (D117, D118). |
| `DERBYNET_LOCK_SETTINGS` | unset | `1` locks the photo and video folder settings, and hides the event controls, for a shared server. |
| `DATABASE_URL` | unset | Another database. See [the database guide](database.md). |
| `POSTGRES_PASSWORD` | `derbynet` | The Postgres password under the `postgres` profile. Set it before the first start. |
| `DJANGO_SECRET_KEY` | unset | The server's secret key. Unset, the server makes one on the volume on its first start. |

Caddy reads these, from `.env`:

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_ADDRESS` | unset | This computer's address on the race's network, such as `192.168.1.20`, for the certificate. Several are separated by commas. |
| `DERBYNET_DOMAIN` | unset | A domain name for a Let's Encrypt certificate. |
| `DERBYNET_ACME_CA` | Let's Encrypt | Another ACME server's directory URL. |
| `DERBYNET_HTTP_PORT`, `DERBYNET_HTTPS_PORT` | `80`, `443` | The ports Caddy publishes. |

## Use your own proxy

A server that already runs a web server or a proxy, such as Nginx Proxy Manager, Traefik or nginx, can serve DerbyNet through it, in place of Caddy. Run the server alone, with `docker run`, and publish its port yourself (D117): the Compose file's server publishes none. Each proxy below needs the same two settings on the server:

- `DERBYNET_TRUSTED_PROXIES`: the address the proxy reaches the container from, so the server believes the proxy when it says a request came over HTTPS, and which client sent it (D118). For a proxy on the host, on Docker's default network, that address is `172.17.0.1`; `docker network inspect bridge` shows it. For a proxy in another container on a shared Docker network, it is that network's subnet, which `docker network inspect <network>` shows.
- `DERBYNET_CSRF_TRUSTED_ORIGINS`: the site's HTTPS address, such as `https://derby.example.org`, so forms posted from it are taken.

None of these three proxies is tested by the project's checks (D107). The server uses no websockets yet, so the websocket settings below change nothing today.

### Nginx Proxy Manager

Start the server on a port of the host, such as 8000:

```sh
docker run -d --name derbynet --restart unless-stopped -p 8000:80 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) -e DERBYNET_TRUSTED_PROXIES=172.17.0.1 -e DERBYNET_CSRF_TRUSTED_ORIGINS=https://derby.example.org -e DERBYNET_ALLOWED_HOSTS=derby.example.org ghcr.io/porkchopexpress86/derbynet_server
```

Then add a Proxy Host in Nginx Proxy Manager:

- Domain Names: `derby.example.org`.
- Scheme: `http`. Forward Hostname / IP: the host's address as Nginx Proxy Manager reaches it, such as `172.17.0.1`. Forward Port: `8000`. When both containers share a Docker network, put the container's name, `derbynet`, and port `80`, and set `DERBYNET_TRUSTED_PROXIES` to that network's subnet instead.
- Websockets Support: on.
- SSL: request a certificate for the domain. Leave "Force SSL" off if kiosks, `derby-timer.jar` or a Pi reach the server by its domain, since they use `http://`.
- Custom headers: none to add. Its proxy configuration sends `X-Forwarded-Proto`, `X-Forwarded-For` and `Host` on its own ([its `proxy.conf`](https://github.com/NginxProxyManager/nginx-proxy-manager/blob/6b26999e343f6daca608475a6783d610595055a9/docker/rootfs/etc/nginx/conf.d/include/proxy.conf)). Under Advanced, add `client_max_body_size 100M;` so replay clips fit.

### Traefik

Start the server on Traefik's Docker network, with these labels, for a Traefik with an entry point named `websecure` and a certificate resolver named `le`:

```sh
docker run -d --name derbynet --restart unless-stopped --network traefik -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) -e DERBYNET_TRUSTED_PROXIES=172.18.0.0/16 -e DERBYNET_CSRF_TRUSTED_ORIGINS=https://derby.example.org -e DERBYNET_ALLOWED_HOSTS=derby.example.org -l traefik.enable=true -l 'traefik.http.routers.derbynet.rule=Host(`derby.example.org`)' -l traefik.http.routers.derbynet.entrypoints=websecure -l traefik.http.routers.derbynet.tls.certresolver=le -l traefik.http.services.derbynet.loadbalancer.server.port=80 ghcr.io/porkchopexpress86/derbynet_server
```

Put your network's name for `traefik`, and its subnet, which `docker network inspect` shows, in `DERBYNET_TRUSTED_PROXIES`. Traefik publishes the port here, so the container needs no `-p`.

### nginx

To add DerbyNet to an existing web site under a path, run the container on a local port, and have nginx pass the path to it. Set `DERBYNET_URL_PREFIX` to that same path, because the server builds its links with it. For DerbyNet at `/my-derbynet` of your site:

```sh
docker run -d --name derbynet --restart unless-stopped -p 127.0.0.1:8000:80 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) -e DERBYNET_URL_PREFIX=/my-derbynet -e DERBYNET_TRUSTED_PROXIES=172.17.0.1 -e DERBYNET_CSRF_TRUSTED_ORIGINS=https://derby.example.org -e DERBYNET_ALLOWED_HOSTS=derby.example.org ghcr.io/porkchopexpress86/derbynet_server
```

In nginx:

```nginx
location /my-derbynet/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    client_max_body_size 100M;
}
```

`Host` keeps links, such as check-in QR codes, on the site's own name. `X-Forwarded-Proto` tells the server when the site was reached over HTTPS, and `X-Forwarded-For` which client sent the request. Your site's own certificate then covers the phones, and nobody sees a certificate warning.

The legacy paths that `derby-timer.jar` and the kiosks use, such as `action.php`, also answer under `/derbynet/`, as older installs served them.

## Back up and restore

A full backup is a copy of the data volume, taken with the server stopped (D65). It holds the whole race: the database, photos, videos, and the server's secret key. With Compose, copy Caddy's volume too, so phones keep trusting its certificate authority after a restore (D120). Keep the backup files private, since they hold the secret key and the authority's private key.

With Compose, on Linux or a Mac, from the folder that holds `compose.yaml`:

```sh
umask 077
docker compose stop
docker run --rm -v derbynet_data:/data:ro --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-data.tgz
docker run --rm -v derbynet_caddy:/data:ro --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-caddy.tgz
docker compose start
```

To restore into new volumes, on this computer or another one:

```sh
docker compose down --volumes
docker compose create
docker run --rm -i -v derbynet_data:/data --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-data.tgz
docker run --rm -i -v derbynet_caddy:/data --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf - < derbynet-caddy.tgz
docker compose up -d
```

Under the `postgres` profile, pass `--profile postgres` to each `docker compose` command, and copy the Postgres volume `derbynet_postgres` the same way. `server/docker/README.md` has those commands.

With the `docker run` setup, the volume is `derbynet`, and the container is stopped with `docker stop derbynet` and started with `docker start derbynet`:

```sh
umask 077
docker stop derbynet
docker run --rm -v derbynet:/data:ro --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -czf - . > derbynet-data.tgz
docker start derbynet
```

On Windows, run these in WSL as written. PowerShell needs two changes, and only version 7.4 or later keeps a command's output byte for byte, which the backup needs:

- Leave out `umask 077`, which PowerShell does not have. Save the backup in your own user folder, not a shared one.
- PowerShell has no `<`. Restore from the folder that holds the backup files, which the container reads directly:

```powershell
docker run --rm -v derbynet_data:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-data.tgz
docker run --rm -v derbynet_caddy:/data -v "${PWD}:/backup:ro" --entrypoint tar ghcr.io/porkchopexpress86/derbynet_server -C /data -xzf /backup/derbynet-caddy.tgz
```

A snapshot from Set-Up is not a full backup. It leaves out videos and some race state (D65).

## Upgrade

With Compose: `docker compose pull`, then `docker compose up -d`. The new server updates the database when it starts. Take a backup first. An install from before Caddy (D107) first needs the new Compose file: see [HTTPS after an upgrade from an older image](#https-after-an-upgrade-from-an-older-image).

With `docker run`, pull the new image, and start a new container on the same volume:

```sh
docker pull ghcr.io/porkchopexpress86/derbynet_server
docker rm -f derbynet
docker run -d --name derbynet --restart unless-stopped -p 80:80 -v derbynet:/var/lib/derbynet -e TZ=America/Chicago -e DERBYNET_UID=$(id -u) -e DERBYNET_GID=$(id -g) ghcr.io/porkchopexpress86/derbynet_server
```

An install from before the server ran as your user keeps its files under the image's own user. The first start with `DERBYNET_UID` gives them to you, once, and logs one line naming both users.

### HTTPS after an upgrade from an older image

Images from before Caddy (D107) served HTTPS themselves, from a certificate in the `tls` folder of the data volume. The new image serves plain HTTP only, so a `docker run` install loses HTTPS when it upgrades. To keep HTTPS, move to Compose, and bring the old volume along. Caddy then serves the old certificate as it is, so no phone has to accept a new one (D119).

From a new folder that holds `compose.yaml` and its `.env` (see [Start DerbyNet](#start-derbynet)), copy the old volume into Compose's, then start Compose:

```sh
docker stop derbynet
docker compose create
docker run --rm -v derbynet:/from:ro -v derbynet_data:/to --entrypoint cp ghcr.io/porkchopexpress86/derbynet_server -a /from/. /to/
docker rm derbynet
docker compose up -d
```

A Compose install from before Caddy has a `compose.yaml` with no Caddy in it, whose server publishes port 443 where the new image no longer listens. Save the new `compose.yaml` over the old one, add the `DERBYNET_ADDRESS` line to `.env` as in [Start DerbyNet](#start-derbynet), then pull and start it. The volume is kept, and Caddy serves its old certificate the same way:

```sh
docker compose pull
docker compose up -d
```

To move from the old certificate to Caddy's certificate authority, delete the `tls` folder and restart Caddy. Each phone then installs the authority once, as [Secure contexts](secure-contexts.md) shows:

```sh
docker compose exec server rm -r /var/lib/derbynet/tls
docker compose restart caddy
```

## Stop, start, and logs

- `docker compose stop` stops DerbyNet, and `docker compose start` starts it again. With `docker run`: `docker stop derbynet` and `docker start derbynet`.
- In Docker Desktop, the Containers list has the same buttons.
- `docker compose logs` shows the server's and Caddy's logs, and `docker logs derbynet` the `docker run` server's. Request bodies are never logged, so no password reaches the log.
