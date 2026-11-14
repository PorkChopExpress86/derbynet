# The database

DerbyNet keeps every event in one database. Most races need no set-up: the container uses SQLite, one file on its data volume. This guide covers that default, Postgres, events, and bringing in races from older versions of DerbyNet.

## SQLite, the default

With no database settings, the server keeps its database in one SQLite file, `derbynet.sqlite3`, on the data volume at `/var/lib/derbynet` (D1). SQLite runs in WAL mode, so kiosks and the timer can read while the race crew writes. A race needs nothing more.

The file is part of the data volume, so [a backup of the volume](install-docker.md#back-up-and-restore) holds it, with the photos and videos.

## Postgres

Postgres is fully supported, through the `postgres` profile of the Compose file (D1). The profile adds a Postgres container with its own volume, and the server finds it with no further settings:

```sh
docker compose --profile postgres up -d
```

- Choose the profile when you first start an install. A data volume remembers the engine it first ran on, in a file named `database-engine` on the volume. The server then refuses to start on the other engine, rather than open an empty event and leave the race in the first database. To start again on the other engine, delete that file.
- Set `POSTGRES_PASSWORD` before the first start to choose the database password. The Postgres container publishes no port, so only the server reaches it.
- Pass `--profile postgres` to every later `docker compose` command for this install, and back up both volumes, `derbynet_data` and `derbynet_postgres`.

### A Postgres server of your own

To use a Postgres server you already run, set `DATABASE_URL` on the container, in the form `postgres://USER:PASSWORD@HOST:5432/DATABASE`. Percent-encode any special character in the password. The server creates its tables on its first start.

## Events

One database holds every event: each pack's race, each year's race, and other events, with one active at a time (D12). Open Set-Up, then "Events: copy, switch, archive, export":

- A new event can copy the settings, the classes and ranks, the award definitions, the kiosk names and the scenes of another event. It never copies racers, results or photos (D14).
- "Make active" switches the server to another event. A past event is read-only until you press "Reopen for editing" (D81).
- "Archive" keeps an event read-only, out of the way.
- "Export zip" saves one event to a file, to move it to another server.

## Bring in an older DerbyNet race

Races from older versions of DerbyNet come in two ways.

### A snapshot

On the older server, the About page saves a snapshot file: its "Complete" download link. On this server, open Set-Up, go to "Snapshots", choose the file under "Snapshot to import", and press "Import snapshot", then "Replace all data". Importing replaces all the data of the active event with the snapshot's: racers, groups, schedules, results, awards and settings. A snapshot does not bring back lane closures, racer notes, check-in times or videos (D65). Before it imports, the server saves a snapshot of the event as it was (D78).

### A whole event with its photos

The importer reads an older DerbyNet database file, `derbynet.sqlite3`, with its racer and car photo folders, and makes it a new event. Copy the file and the folders into the container, then run the importer as the server's own user:

```sh
docker cp /path/to/pack42 derbynet:/tmp/pack42
docker exec -u derbynet derbynet python manage.py import_derbynet /tmp/pack42/derbynet.sqlite3 --event "Pack 42 2025"
```

Stop the older server before you copy its database. The importer reads every schema version of older DerbyNet databases, from 1 to 10, and prints a summary of the rows it read, dropped and changed. `--report report.json` writes the full report. `server/README.md` lists the importer's options.
