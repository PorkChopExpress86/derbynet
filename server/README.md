# DerbyNet Django server

This directory holds the Django port of the DerbyNet server. The PHP server in `website/` keeps running until the cutover release (ADR-01, ADR-13). The architecture decisions are in [`docs/adr/`](docs/adr/README.md).

## Set up

Install [uv](https://docs.astral.sh/uv/). It installs Python 3.14 for you if the host lacks it. From this directory, run:

```sh
uv sync
```

`uv sync` creates `.venv/` with the locked versions from `uv.lock`, including the development tools.

## Test and lint

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python manage.py makemigrations --check --dry-run
```

The `server` GitHub Actions workflow runs these four commands on Python 3.14, once on SQLite and once on Postgres. To run the tests on Postgres locally, set `DATABASE_URL`, for example `DATABASE_URL=postgres://postgres:postgres@localhost:5432/derbynet uv run pytest`.

## Run both servers

From the repository root, first build the files the PHP pages need, as step 2 of the top-level `README.md` says (`ant generated`). Then run:

```sh
docker compose -f server/compose.yaml up
```

Compose starts three services. They share no database.

- `php` runs the `jeffpiazza/derbynet_server` image with `website/` mounted read-only, on port 8080.
- `php-setup` creates an event database for `php` on the first start, then exits. Without one, PHP redirects every request to `setup.php`.
- `django` runs `manage.py migrate` and then `manage.py runserver 0.0.0.0:8000` with `server/` mounted, so code edits reload.

Check that both answer:

```sh
curl -s 'http://localhost:8080/action.php?query=role.list'
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/
```

These variables change the defaults:

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_PHP_PORT` | `8080` | Host port for `php` |
| `DERBYNET_DJANGO_PORT` | `8000` | Host port for `django` |
| `DERBYNET_WEBSITE_DIR` | `../website` | The PHP document root to mount, relative to `server/` |

`docker compose -f server/compose.yaml down -v` stops the servers and deletes their data.

## Point a test script at either server

The scripts in `testing/` take the server's base URL as their first argument. Pass the PHP server or the Django server:

```sh
testing/login-coordinator.sh http://localhost:8080
testing/login-coordinator.sh http://localhost:8000
```

The Django server answers no legacy actions yet, so the scripts pass only against PHP until `/action.php` lands (#18).

## Settings

`derbynet/settings.py` reads every install-level setting from the environment.

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_DATA_DIR` | `server/var/data` | Root for event folders and media |
| `DERBYNET_CONFIG_DIR` | `server/var/config` | Holds `secret_key` and other install-level files |
| `DATABASE_URL` | `sqlite:///<DERBYNET_DATA_DIR>/derbynet.sqlite3` | Parsed with `dj-database-url` |
| `DERBYNET_URL_PREFIX` | empty | Sets `FORCE_SCRIPT_NAME`, for example `/derbynet` |
| `DERBYNET_TIME_ZONE` | `TZ` when it is an IANA name, else `UTC` | Sets `TIME_ZONE`, with `USE_TZ = True`. An invalid value raises `ImproperlyConfigured` |
| `DERBYNET_DEBUG` | `0` | Sets `DEBUG`. Only `0` and `1` are accepted |
| `DERBYNET_ALLOWED_HOSTS` | `*` | Comma-separated. LAN installs are reached by bare IP address |
| `DERBYNET_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated extra origins for the new UI behind a proxy |
| `DJANGO_SECRET_KEY` | unset | When unset, the server generates a key into `<DERBYNET_CONFIG_DIR>/secret_key` with mode 0600 on first start, and reuses it after that |

SQLite runs in WAL mode with `synchronous=NORMAL`, `IMMEDIATE` transactions, and a 20 second busy timeout (ADR-05). WAL writes `-wal` and `-shm` files next to the database, so keep `DERBYNET_DATA_DIR` on a writable local disk. A database on a network share can be corrupted. A Postgres `DATABASE_URL` is used as `dj-database-url` parses it, with no extra options.
