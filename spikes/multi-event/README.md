# Spike: one database per event versus an `Event` foreign key

This spike is throwaway evidence for issue #9. It is not production code. It compares two ways for a Django 5.2 server to hold more than one race-day event:

- Option (a) keeps one database per event. A `site` database holds `SiteState`, which stores the active event slug and a version counter. The `event` alias points at the active event's database. The middleware in `spike/middleware.py` reads `SiteState` on every request. When the version changed, the middleware closes the `event` connection and rewrites the connection's `NAME`.
- Option (b) keeps one database. It has `Event` rows, `SiteState.active_event`, and an `event` foreign key on `Racer`, `Heat`, `Result`, and `TimerState`. The middleware resolves the active event once per request, and every view filters on it.

`SPIKE_MODE` (`a` or `b`) and `SPIKE_DB` (`sqlite` or `pg`) select the variant. Both variants serve the same three polling endpoints and one switch endpoint:

| Endpoint | Stands in for | What it does |
| --- | --- | --- |
| `GET /kiosk/poll` | Kiosk | Reads the running heat and its racers |
| `GET /coord/poll` | Race coordinator page | Counts racers, heats, and results, and reads the last 10 results |
| `POST /timer/heartbeat` | `derby-timer.jar` heartbeat | Writes `TimerState.last_contact` |
| `POST /switch?to=<slug>` | Event switch | Option (a) migrates the target database, then increments `SiteState.version`. Option (b) updates `SiteState.active_event`. |

Every response carries two event identities. `event` is the event the server says it served. `rows_event` comes from the racer rows that the query returned, because every racer name starts with its event slug. `loadgen.py` counts a request as failed when the status is not 200, when the request raised, or when the two identities differ. It counts a request as stale when the client sent it after the switch committed and the server answered from an event other than the switch target. The switch response carries the server's commit timestamp. The client and the server run on one host and share one clock.

## Results

These results come from one WSL2 host with 8 cores, Python 3.14.4, Django 5.2.18, gunicorn 26.2.0, and `postgres:17` in Docker. `results/` holds the summaries, and `results/raw.tar.gz` holds every request record.

### Switching under the issue's load

Each configuration ran 5 times, interleaved with the others. Each run had 4 sync workers, 10 kiosks at 1 request every 5 s, 1 coordinator every 2 s, 1 timer heartbeat every 500 ms, and 20 switches 6 s apart. The load generator reached the offered 4.5 requests per second in every run.

| Config | Requests | Failed | Stale | Switch request, median ms | All 4 workers on the new event, s |
| --- | --- | --- | --- | --- | --- |
| (a) SQLite WAL | 2819 | 0 | 0 | 10 (8 to 15) | median 2.05, max 5.50 |
| (a) Postgres, one database per event | 2818 | 0 | 0 | 40 (37 to 49) | median 2.04, max 5.09 |
| (b) SQLite WAL | 2818 | 0 | 0 | 4 (4 to 5) | median 2.49, max 5.05 |
| (b) Postgres | 2819 | 0 | 0 | 12 (11 to 13) | median 1.99, max 4.99 |

The parentheses give the range of the per-run medians. Every one of the 400 switches committed. `python aggregate.py results/matrix` prints the full table.

The "all workers" time is how long the last of the 4 workers took to receive any request after the commit. No worker answered a post-commit request from the old event, so this time measures request arrival, not the switch mechanism. At 10 times the request rate (45 requests per second, 30 switches 2 s apart, one run per configuration), the same number fell to a median between 0.14 s and 0.30 s, still with 0 failed and 0 stale.

Option (a) switches cost more because the switch runs `migrate` on the target database before it commits. On an up-to-date database that check took a per-run median of 5 to 10 ms on SQLite and 26 to 37 ms on Postgres.

### Migrate on open, option (a)

`spike/migbench.py` builds an event at `0001`, seeds it, and times one `migrate` to `0005`. Each row is the median of 5 runs, with the range.

| Engine | 100 racers, 400 results | 1000 racers, 4000 results | Already at head | Create and migrate a new event |
| --- | --- | --- | --- | --- |
| SQLite | 121 ms (99 to 137) | 538 ms (524 to 576) | 14 to 16 ms | 75 ms |
| Postgres | 123 ms (102 to 179) | 555 ms (266 to 655) | 40 to 49 ms | 218 ms, of which `CREATE DATABASE` is about 55 ms |

The data migration `0005` is most of that time. A profile of one 1000-racer run put 412 ms of 472 ms in `bulk_update`, mostly in ORM expression building. Each schema migration took 5 to 14 ms. When `0005` runs alone (`spike/permig.py`), it takes 144 to 164 ms on SQLite. I did not find why the same migration is about three times slower inside a longer plan.

### Failures found outside the issue's load

- **Two simultaneous switches to an event at an older migration.** `race_switch.sh` sends two switch requests at once to an event at `0001`. I ran two batches of 5 pairs per engine. `results/extra/race-*.txt` holds the second batch. On SQLite, 7 of 10 pairs returned one HTTP 500 (`table "eventapp_award" already exists`). On Postgres, 6 of 10 pairs returned one HTTP 500 (`column "note" of relation "eventapp_racer" already exists`). In the second batch of 5 SQLite pairs, 2 events ended with `0005` recorded as applied and its data change missing for all 1000 racers, and 3 events had duplicate `django_migrations` rows. Option (a) needs a migration lock per event database. Option (b) runs migrations once at deploy.
- **Version tracked per process.** `SPIKE_FAULT=process_version` stores the last seen version per process instead of per connection. On sync workers it behaves the same as the real middleware. With `-k gthread --threads 4`, 1031 of 2926 requests were stale. The real middleware had 0 stale requests on gthread workers.
- **Connections on Postgres.** During an option (a) run, `pg_stat_activity` showed 4 connections to `spike_site` and 4 to event databases, split across two events. A worker keeps its connection to the old event until its next request. Option (b) used 4 connections.

## Files

- `spike/settings.py` defines both variants and the per-event connection settings.
- `spike/middleware.py` holds the option (a) repoint middleware and the option (b) resolver.
- `spike/events_a.py` creates, migrates, and switches option (a) event databases.
- `eventapp/migrations/` holds five migrations, so an event can sit at an older state. `0003` rebuilds the racer table on SQLite. `0005` is a data migration over every racer.
- `spike/setup.py` resets the data and seeds events `e1`, `e2`, and `e3` with 100 racers, 100 heats, and 400 results each.
- `spike/migbench.py` times `migrate` from `0001` to the head. `spike/permig.py` times each migration step. `spike/make_old.py` creates events at `0001`.
- `loadgen.py` drives the load and the switches and prints a JSON summary. `loadgen.py --reanalyze` recomputes a summary from saved records.
- `run_one.sh` runs one configuration. `run_matrix.sh` runs every configuration, interleaved. `aggregate.py` prints the table. `race_switch.sh` runs the simultaneous-switch test.

## Run it

You need Python 3.12 or later, `curl`, and Docker for the Postgres variants. Run these commands from this directory.

```sh
python3 -m venv .venv
.venv/bin/pip install 'Django>=5.2,<5.3' gunicorn 'psycopg[binary]'
docker run -d --name m0spike-pg -e POSTGRES_PASSWORD=spike -p 127.0.0.1:8090:5432 postgres:17
./run_matrix.sh out/matrix 5
.venv/bin/python aggregate.py out/matrix
./race_switch.sh sqlite 5
SPIKE_MODE=a SPIKE_DB=sqlite SPIKE_DATA=.data/mig .venv/bin/python -m spike.setup e1
SPIKE_MODE=a SPIKE_DB=sqlite SPIKE_DATA=.data/mig .venv/bin/python -m spike.migbench 100,1000 5
```

`run_matrix.sh` takes about 45 minutes. `run_one.sh` reads `VENV`, `PORT` (default `8091`), `SPIKE_PG_PORT` (default `8090`), `SPIKE_FAULT`, and `GUNICORN_ARGS` (default `-w 4`). Arguments after the third go to `loadgen.py`, for example `--scale 10 --switches 30 --period 2`.

## Controls

`SPIKE_FAULT` injects a known defect, so you can confirm that `loadgen.py` reports it.

- `no_repoint` makes option (a) workers ignore every switch. A 3-switch run reported 27 stale requests.
- `process_version` makes option (a) track the version per process. It reported 1031 stale requests on gthread workers.
- `leak` drops the event filter from the option (b) kiosk query. A 3-switch run reported 12 failed requests. The leak is invisible while the first event is active, because the unfiltered query returns that event's rows. A cross-event test must make a later event active.

## Limits

- Field clients poll slowly, so a switch reaches each worker only when that worker gets its next request. The load was light. The p99 latency of every endpoint stayed under 65 ms.
- The events are small: 100 racers. The migration history is five synthetic migrations, not the real schema.
- The Postgres runs used the `postgres` superuser. I did not test a role without `CREATEDB`.
- The host is a shared WSL2 machine. Interleaving the configurations spreads its noise across all four.
