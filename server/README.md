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

`racing.services.schedule.generate_schedule(round, n_times_per_lane)` replaces a round's chart and returns its number of heats. `unschedule(round)` deletes it. On failure `generate_schedule` raises `ScheduleFailure` with a `code` of `nlanes`, `results-exist`, `too-few-racers`, `cant-fulfill` or `busy`, and the old chart stays. `cant-fulfill` covers fewer than one run per lane and every request the generator tables cannot meet. Under normal scheduling on two or more lanes, that is more runs per lane than the tables give, 7 lanes, more than 8 lanes, or more than 200 racers. One lane (up to 6 runs per lane), rotation scheduling (any number of runs), and `max-runs-per-car` (exactly one run) do not need a table, so they schedule any racer and lane count. PHP reports success on these requests and writes an empty round, or answers 500. `racing.engine.scheduling.rough.max_runs_per_lane(nlanes, nracers)` tells a form how many runs per lane the tables give.

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

## Master schedule

`generate_schedule` and `unschedule` renumber the event's master heats in the same transaction as the chart, through `racing.services.master.reassign_master_heats(event)`. As in PHP, this runs whether `use-master-sched` is on or off. Raced heats keep their numbers. The unraced heats are numbered from 1 above the highest raced number, interleaved across the rounds of each round level by weight. The engine is `racing/engine/scheduling/master.py`. `assign_master_heats` does the numbering, and `master_target` gives the heat that `next-up`, `next` and `prev` land on. `racing/engine/tests/test_master_parity.py` checks the numbering against `tests/golden/http/master-schedule/half-weights.http`.

## Groups, rounds and the playlist

`roster/services/groups.py` holds the partition, group (legacy class) and subgroup (legacy rank) services behind `action.partition.*`, `action.class.*` and `action.rank.*`, and `form_groups_by_rule(event, rule)` for the three group formation rules. `racing/services/rounds.py` holds `prepare_new_round`, `write_roster(round, finalists)`, `delete_round_roster`, `round_counts` and `ready_aggregate_groups`. `racing/services/playlist.py` runs the playlist: `start_playlist`, `round_ended`, and the operator gate. When a round ends and the next step has `waits_for_operator`, the next round is scheduled and selected, racing stays off, and `RaceState.waiting_entry` and `ended_round` hold the intermission until `start_waiting_step(event)` starts it or `skip_waiting_step(event, finalists_for)` passes over it to the next step (D28). `rebuild_waiting_round` makes a waiting round's roster and chart again after a result in the round that feeds it changes (D27). A domain failure raises `GroupFailure`, `RoundFailure` or `PlaylistFailure` with PHP's code. Every service that changes a roster takes the `RaceState` lock first, as `generate_schedule` needs.

Owner decision D31 sets two rules where PHP behaves otherwise.

- A real rule change deletes every round, chart, aggregate link, group award and playlist step, and is refused with `results-exist` once any heat has a result. Applying the rule already in force writes nothing and succeeds, even once results exist.
- Partition names are unique per event with case ignored. Adding or renaming a partition to a name that matches another partition in any case is refused with `name-taken` (an exact match on `partition.add` keeps PHP's `already-exists`). `find_or_create_partition` returns the partition that matches, so a racer imported with `LIONS` joins `Lions`. The services compare `Partition.name_key`, Python's `casefold()`, because SQLite's `lower()` folds only ASCII letters. The importer renames a legacy partition that matches an earlier one in any case.

A final with fewer than 2 racers, zero included, gets D28's "The final needs at least 2 racers." from `start_waiting_step` (D34). Only a waiting round that was deleted during the intermission, and so is remade empty, says "The waiting round has no racers in its roster."

The finalists of a follow-on or aggregate round come from the standings (see below), so `write_roster` and the playlist services take them as input. `racing/engine/groups/aggregates.py` orders the aggregate groups (Q8), computes what each draws from, and finds the aggregates ready for a roster.

`roster/tests/test_group_scenarios.py` and `racing/tests/test_round_scenarios.py` replay the group and round scripts of the shell suite through these services, using `roster/tests/php_replay.py`, and compare the state with what PHP recorded. PHP rebuilds the groups, with new ids, when a script applies the rule in force, so the replay checks that Django changes nothing and then rebuilds them itself, so later requests find the rows they name.

## Standings and speed awards

`racing/engine/standings/` ports PHP's standings (`website/inc/standings.inc`), finalist cut (`website/inc/roster.inc`), speed awards (`website/inc/awards.inc`), award names (`website/inc/ordinals.inc`) and lane bias test (`website/inc/lane-bias.inc`) as pure Python. `oracle.Standings(event)` gives the result rows, best first, each with its places, and the ladders keyed as PHP keys them: `st-r<round>`, `st-rr<round>-<subgroup>`, `st-q<group>` and `st-ff`. Scores follow PHP's SQLite rules on every database: `math.fsum` for `SUM`, NULLs left out as SQL leaves them, a single heat scoring as itself under drop-slowest (Q12), and names compared as `NOCASE`, which folds ASCII letters only.

`racing/services/standings.py` reads an event with a few ORM queries. `standings_snapshot(event)` returns the standings, and its `speed_awards`, computed from the rows as they are. It caches nothing: reading the rows is most of the time (about 21 ms of 28 ms for 200 racers and 1200 results), so a cache would save little even for the award kiosk's 0.5 s `award.current` poll. The ladders and orders it returns are tuples and read-only mappings. `current_award(event)` resolves `RaceState.current_award_key` for speed awards. `racing/services/lane_bias.py` holds `lane_bias(event)`.

`racing/services/rounds.py` holds `new_round_roster(source, top, bucketed, extras)`, the service behind `roster.new`. The source is `FollowOn(round)`, `FromAggregate(group)` or `NewAggregate(name, groups, subgroups)`; a new aggregate becomes the `full-field-calc` group, as in PHP. In one transaction it makes the round or clears its roster, cuts the standings, and enters the qualifiers, then the extra racers the race coordinator allowed in (D32), which never take a qualifier's place. When the roster would be empty it fails with `no_racers` and changes nothing. `playlist_finalists(extras)` is the `finalists_for` the playlist services take, with each final's extras keyed by its group and round number, so a D27 rebuild that passes the same extras keeps them and no other final gets them. It returns the racers with the cut's concerns, for the intermission to show (D28).

Award eligibility follows owner decisions D33, D34 and D35:

- A racer has two award settings. `Racer.exclude` is "not eligible for any award", and `Racer.not_eligible_for_speed_awards` is new. The legacy API reads and writes `exclude` only.
- Every award unit lists the partitions its awards can go to: the pack, each group (aggregates included), each subgroup, and later each judged award (#33). `awards.models.AwardPartition` stores a list only where it is narrowed, and `awards/services.py` reads and sets them. No stored list means every partition, those added later too; a narrowed list keeps exactly its partitions; deleting a partition deletes it from every list, and a list left empty goes back to all partitions. The legacy API neither reads nor writes the lists.
- A racer who cannot win a ladder's speed awards keeps the standings rows, marked in `Row.not_eligible` with the reason, and takes no place and adds no `T` mark there. Every other place, ladder, speed award and finalist is then what PHP gives with that racer removed. A racer whose partition a unit's list leaves out is marked only in the ladder that unit's awards read, and keeps every other place. The `full-field-calc` group's award ladder is the pack's.
- A cut skips any racer who cannot win the speed awards of the group the new round is for, and takes the next racer. It reads that group's list, never the pack's. `racing/engine/standings/eligibility.py` holds the one rule the ladders, the awards and the cut share.

`racing/engine/tests/test_standings_parity.py`, `test_speed_awards_parity.py`, `test_ordinals.py` and `test_lane_bias.py` compare the engine with every PHP standings, award and engine fixture, and `racing/tests/test_finalists.py` replays every `roster.new` and playlist cut of the shell suite. To time `standings_snapshot()` and the speed awards on a 200-racer event with 1200 results:

```sh
export DATABASE_URL=sqlite:////tmp/standings-timing.sqlite3
uv run python manage.py migrate -v 0
uv run python -m tools.time_standings
```

## Standings differential test against PHP

`tools/standings_differential/` runs the real PHP standings, finalists and speed-award code and the Django engine in `racing/engine/standings/` on the same seeded random events, and compares every result row, ladder, catalog entry, class and rank key, finalist cut, speed award and `award.current` answer. It needs Docker and a local image with `php84` and the website at `/var/www/html`. From this directory, run:

```sh
uv run python -m tools.standings_differential [IMAGE] --controls
```

`IMAGE` defaults to `derbynet-local`. The run makes 2000 random events and 200 events for each targeted batch in `generate.py`, takes about two minutes, and exits 1 if any comparison differs. `--controls` also breaks the engine in a dozen seeded ways and checks that the comparison reports each. Each PHP run is a `docker run --rm` container named after `--name-prefix`, with no volume. pytest does not run the tool against PHP, because CI has no PHP image.

The comparison counts three deliberate differences apart, Q7, Q8 and Q22 (#20). A key whose place ends in two or more letters, such as `speed-1ab`, names the 28th award of a tie in Django, where PHP reads its last letter only. It counts as Q22 only when Django answers with its own award of that key, or with nothing when it has none. An event counts as Q7 only when a model of PHP's award loop, changed only in its tie indexing, gives Django's awards one way and PHP's the other. A `current_award_key` with an oddly written number, such as `speed-02` or `speed-2- 1`, is compared like any other: the engine reads it with PHP 8's comparison rules. A key that makes PHP's `award.current` throw, such as `speed-x1`, is recorded as naming no award, which is what the engine answers. `racing/engine/tests/test_differential_controls.py` runs every seeded fault in CI against the engine's own answers.

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
