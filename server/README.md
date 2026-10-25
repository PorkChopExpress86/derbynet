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

Model tests build their data with the factory_boy factories in `<app>/tests/factories.py`. `EventFactory` creates an event through `core.services.new_event()`, so each event comes with its settings, state rows, and the six legacy scenes.

## Schedule generator tables

`racing/engine/scheduling/generator_tables.py` holds the PHP schedule generator tables (`website/inc/generators/*.inc`) as data. Do not edit it. Regenerate it, and commit the result, with:

```sh
uv run python tools/convert_generators.py
uv run python tools/convert_generators.py --check   # exits 1 if the module is out of date; CI runs this
```

`racing.engine.scheduling.generators.scheduler_data(nlanes, ncars)` returns `{"generators": [...], "permutation_sets": [...]}` or `None` when no table covers the counts. The module loads on the first call. `racing/engine/tests/test_generator_tables.py` compares the tables with the PHP dump in `tests/golden/engine/scheduler-data/`.

## Schedule generation

`racing.services.schedule.generate_schedule(round, n_times_per_lane)` replaces a round's chart and returns its number of heats. `unschedule(round)` deletes it. On failure `generate_schedule` raises `ScheduleFailure` with a `code` of `nlanes`, `results-exist`, `too-few-racers`, `cant-fulfill` or `busy`, and the old chart stays. `cant-fulfill` covers every request the generator tables cannot meet: too many runs per lane, 7 lanes, more than 8 lanes, and more than 200 racers. PHP reports success there and writes an empty round, or answers 500. `racing.engine.scheduling.rough.max_runs_per_lane(nlanes, nracers)` tells a form how many runs per lane the tables give.

The engine modules in `racing/engine/scheduling/` are ported from `website/inc/schedule_*.inc`. `ordered.py` keeps the literal port of PHP's greedy reorder as `reference_ordered_schedule`, and `make_ordered_schedule` makes the same choices in integer arithmetic. The parity tests compare both with the PHP fixtures:

```sh
uv run pytest racing/engine/tests/test_schedule_parity.py -m pr     # every pull request, about a minute
uv run pytest racing/engine/tests/test_schedule_parity.py -m full   # the whole sweep, in the schedule-parity workflow
```

A plain `uv run pytest` runs the `pr` grid and skips `full`.

To time the largest case, 200 racers on 6 lanes with 6 runs per lane (1200 heats):

```sh
uv run python -m timeit -n 3 -r 3 -s 'from racing.engine.scheduling.ordered import make_ordered_schedule' 'make_ordered_schedule(6, 200, 6, 0)'
```

## Inspect an event

The server has no Django admin (ADR-16). To see what one event holds, print its rows for every event-scoped model as JSON:

```sh
uv run python manage.py dump_event <slug>
```

The command only reads.

## Import a past DerbyNet event

`import_derbynet` loads one event from the PHP server as a new event. The source is an event folder's `derbynet.sqlite3`, at any schema version from 1 to 10, or a file saved from `action.php?query=snapshot.get&photos`. The importer copies a SQLite source, with its `-wal` and `-journal` files, to a private temporary folder and reads the copy read-only, so the source folder is never written and can be read-only. A source with an unfinished write in its `-journal` is refused. Stop the PHP server before importing its database.

```sh
uv run python manage.py import_derbynet /path/to/2024/pack42/derbynet.sqlite3 --report report.json
```

| Option | Effect |
|---|---|
| `--event NAME` | The new event's name. Default: the source's folder name, `pack42` above. |
| `--photos-dir DIR`, `--car-photos-dir DIR` | Where to look for photos first. Then the source's `racers/` and `cars/` folders. |
| `--replace` | Replace the event of the same name. Its rows go, and after the commit its `racers/`, `cars/`, `videos/` and `logs/` folders go too. The new event starts with empty `videos/` and `logs/`. Without `--replace`, an existing name is refused. |
| `--time-zone ZONE` | The IANA time zone the PHP server kept local times in, such as `America/Chicago`. Default: this server's `TIME_ZONE`. The report records it. |
| `--accept-violations` | Keep the import when `check_invariants()` finds violations. Without it, they roll the import back. |
| `--report PATH` | Write the full report as JSON. A summary always goes to stdout. The path cannot be the source or its `-wal`, `-shm` or `-journal` file. |
| `--force-report` | Overwrite an existing `--report` file. Without it, an existing file is refused before the import starts. |

The report counts the rows read and imported per legacy table, and lists every row dropped and every value changed, with the reason.

The import copies photos into a new folder, `events/<name>.import-<token>` under `DERBYNET_DATA_DIR`, and points the event's photo, video and log directories at it in the same transaction as the rows. A killed import leaves either the old event with its folders or the new event with the new folder. The next import removes what a killed one left behind and lists it in the report under `recovered`. With `--replace`, the old event's folders are removed after the commit.

The tests read fixtures that `transfer/tests/fixtures/legacy/build.sh` made with the PHP server.

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

The Django server serves `/action.php` with PHP's dispatch rules and envelope. Only the login handlers are ported, so the `user_login_*` and `user_logout` helpers of `testing/common.sh` pass against both servers. Every other name answers `unrecognized` until M3 ports it.

## Legacy handlers

A handler lives in `legacy_api/handlers/` and registers under its exact PHP name with `@action(...)` or `@query(...)` from `legacy_api/registry.py`. Each declares its `permission`, a `Perm` from `accounts/permissions.py` or an `Access` value. The dispatcher checks it against the session's `role` and `permissions` keys before the handler runs. A handler resolves every id from a request with `ctx.resolve(Model, raw_id)`, which finds rows of the active event only, and writes through services, never the ORM. Each new endpoint adds a request to `LEAK_REQUESTS` in `legacy_api/tests/test_event_leak.py`.

## Roles and logins

Roles are rows of `accounts.Role`, with hashed passwords and PHP's permission bits (ADR-07, ADR-16). A new database starts with PHP's five default roles and passwords, which `testing/default.passwords` lists. A login stores the role name and its bits in the session. A session with no login holds the bits of the anonymous role, the one named `''`. Before the first event exists, every session also holds `SET_UP`. `Access.LOGGED_IN` admits only a role that has a password. Anyone can log in as `Timer`, because its password is empty, so a timer handler requires `TIMER_MESSAGE` instead. A logout clears the whole session.

A session keeps the bits it got at login. A role edit or an `import_roles` run does not change a session that is already logged in, until that session expires after 24 hours. PHP behaves the same way.

To keep the roles and passwords of a PHP install, import its `config-roles.inc`. The import replaces every role:

```sh
uv run python manage.py import_roles /path/to/local/config-roles.inc --dry-run
uv run python manage.py import_roles /path/to/local/config-roles.inc
```

`--dry-run` prints each role and its bit mask and writes nothing. When the file holds PHP that the command cannot read, it runs `php` instead, if `php` is installed. That executes the file as PHP, with `--dry-run` too, so import only a file you trust.

Pages log in at `login/` and log out with a POST to `logout/`. A page view declares its permission with `@page_requires(Perm.X)` from `accounts/views.py`, which sends a visitor without it to `login/?next=<page>`. A mobile check-in link is `mcheckin.php?tok=<make_mcheckin_token()>`. It grants check-in, revert and photo upload to the phone's session, and expires after 60 minutes.

A POST to `/action.php` that carries an `Origin` header for another site answers `notauthorized`. The server's own origin and the origins in `DERBYNET_CSRF_TRUSTED_ORIGINS` pass. Hardware clients send no `Origin`.

Behind a proxy that ends TLS, the server takes its own scheme from the `X-Forwarded-Proto` header. If the proxy does not send that header, the server thinks it serves `http`, and every browser POST to `/action.php` answers `notauthorized`, logins included. Set the proxy to send `X-Forwarded-Proto: https`, or set `DERBYNET_CSRF_TRUSTED_ORIGINS` to the public origin, such as `https://derby.example`.

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
