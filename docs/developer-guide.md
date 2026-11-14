# Developer guide

This guide is for people who change DerbyNet's server. The server is a Django project in `server/`. The PHP server in `website/` stays in the repository until the cutover release, and gets no fixes (D12). Every fix goes into `server/`.

The details live beside the code. This guide says where things are, and points to them:

- `README.md`: running a development server in the Docker image.
- `server/README.md`: the server's set-up, tests, tools, settings, and each part of the port.
- `server/docker/README.md`: the image a race runs.
- `server/docs/adr/README.md`: the architecture decisions.
- `testing/README.md`: the shell test suite.
- `server/tests/golden/README.md`: the fixtures recorded from PHP.

## Set up

Install uv (<https://docs.astral.sh/uv/>), then from `server/`:

```sh
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

`uv sync` installs Python 3.14 and the locked packages. The first visit to `http://127.0.0.1:8000/` sets up an event.

To work against the image a race runs, follow the development steps in `README.md`. They mount your `server/` folder into the image.

## The layout of server/

| Folder | What it holds |
|---|---|
| `server/derbynet/` | The Django project: settings, URLs, WSGI. Every install setting is an environment variable, listed in `server/README.md`. |
| `server/core/` | Events, race settings, the race-state lock, diagnostics, and the database invariants. |
| `server/accounts/` | Roles, passwords, permission bits, and login. There are no Django users (D22). |
| `server/roster/` | Racers, partitions, groups and subgroups, car numbers, and check-in. |
| `server/racing/` | The schedule generator, rounds, heats, results, standings, the timer, and replay. |
| `server/awards/` | Awards, judging, and presentation. |
| `server/kiosks/` | Kiosks, scenes, the kiosk pages' registry, and the message relay. |
| `server/media/` | Photos, slides, and replay videos. |
| `server/transfer/` | Snapshots, exports, and the importer for older DerbyNet databases. |
| `server/legacy_api/` | `action.php`: the endpoints that kiosks, timers and photo stands call, with PHP's names and answers. |
| `server/ui/` | The manager pages, the kiosk pages, these guides under `/docs/`, and the browser tests. |
| `server/tests/` | Tests across apps: the golden fixtures, and the source checks. |
| `server/tools/` | Scripts for tests on Postgres, load runs, and checks. |
| `server/docker/` | The image a race runs. |

Each app keeps its tests in its own tests folder. A view or a handler is thin: it reads the request, calls a service, and draws the answer. Services hold the rules, and take the race-state lock before they change race data (`core.services.lock_race_state`).

## Run the tests

From `server/`:

```sh
uv run pytest -n auto
uv run ruff check
uv run ruff format --check
uv run python manage.py makemigrations --check --dry-run
```

CI runs the same commands on SQLite and on Postgres. A test can pass on SQLite and fail on Postgres. `server/tools/pytest-postgres.sh` runs pytest against a throwaway Postgres container. `server/tools/source-checks.sh` runs, in seconds, the tests that read only the source.

`git config core.hooksPath .githooks` turns on the pre-push hook, which runs the lint checks.

### The browser tests

The Playwright tests in `server/ui/tests/browser/` drive the pages in Chromium. They run only when asked:

```sh
uv run playwright install chromium
uv run pytest -m browser ui
```

Each test runs twice: on a phone, at 390x844 with touch, and on a desktop, at 1280x800. A console error, a page error, or a failed request fails the test unless the test allows it. `shoot(page, name)` saves a screenshot. `server/README.md` lists the helpers, and how to draw the fonts CI draws.

## Run the shell suite against Django

The scripts in `testing/` drive a server over HTTP, the same way for PHP and for Django. Point one at a Django server that has the test endpoints on:

```sh
cd server
export DERBYNET_ENABLE_TEST_ENDPOINTS=1
uv run python manage.py migrate
uv run python manage.py create_test_event
uv run python manage.py runserver 127.0.0.1:8000 &
../testing/reset-database.sh http://127.0.0.1:8000
```

The scripts need `curl`, `jq` and `xmllint`. `DERBYNET_ENABLE_TEST_ENDPOINTS` turns on `test.setup`, which no race may have. The `server` workflow in `.github/workflows/server.yml` lists the suites CI runs against Django. A suite joins it once it passes on SQLite and on Postgres.

To see what PHP does with one script, `testing/docker/php-scratch.sh` runs it against the PHP server in a container. `testing/README.md` explains it.

## Add a legacy endpoint

Kiosks, timers and photo stands call `action.php` with PHP's endpoint names. To add one:

1. Write the handler in a module of `server/legacy_api/handlers/`. Register it under its PHP name with `@action("name", permission=...)` or `@query("name", permission=...)` from `server/legacy_api/registry.py`.
2. Give it a permission: a `Perm` from `server/accounts/permissions.py`, or an `Access` value. The dispatcher checks it before the handler runs.
3. Find each row through `ctx.resolve(Model, raw_id)`, which finds rows of the active event only. Change data only through a service, never through the ORM in the handler. `server/legacy_api/tests/test_registry.py` fails a handler that writes through the ORM.
4. Add a request for it to `LEAK_REQUESTS` in `server/legacy_api/tests/test_event_leak.py`.
5. When its answer differs from PHP's on purpose, list each difference in its `divergences`, with the decision or quirk that allows it.

`server/README.md`, section "Legacy handlers", has the details.

## The golden fixtures

`server/tests/golden/` holds what the PHP server answered, recorded by running the shell suite against it. Django's answers are compared with them. A difference the owner accepts is listed in `server/tests/golden/deviations.json` (D25). Never edit a fixture by hand: `server/tests/golden/capture.sh` records them all again, from a regular clone of the repository, not a git worktree. `server/tests/golden/README.md` explains each kind of fixture.

## The guides

The guides are Markdown files in `docs/`, with their images in `docs/img/<guide>/`. The server shows them under `/docs/`, and the help link in the menu opens `/docs/quick-reference/`.

- `manage.py build_docs` renders them to HTML into `DERBYNET_DOCS_ROOT`, by default `server/var/docs`. The image renders them when it is built. Run it again after you change a guide.
- `server/ui/docs/build.py` lists the guides in `GUIDES`. A new guide goes into that list.
- `server/docs/adr/check-paths.sh --docs docs` checks every relative link, every image, and every path in backticks, and the house style. CI runs it.
- `docs/tools/fodt-to-md.sh` made the first draft of each guide from the LibreOffice files the PHP version shipped.

Write sentence-case headings and short, plain sentences. A guide states only what the code does: name the setting, the label, or the URL as the server shows it.
