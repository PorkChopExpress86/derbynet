# Architecture decisions for the Django port

These architecture decision records (ADRs) record how the DerbyNet server moves from PHP to Django. The source spec is [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4). Later specs and tickets build on these decisions and cite them by number.

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-01](0001-build-in-parallel-and-cut-over-once.md) | Build in parallel and cut over once | Accepted |
| [ADR-02](0002-keep-the-legacy-wire-contract-permanently.md) | Keep the legacy wire contract permanently | Accepted |
| [ADR-03](0003-use-django-5-2-lts-on-python-3-14.md) | Use Django 5.2 LTS on Python 3.14 | Accepted |
| [ADR-04](0004-use-managed-models-with-fresh-migrations-and-a-one-way-importer.md) | Use managed models with fresh migrations and a one-way importer | Accepted |
| [ADR-05](0005-default-to-sqlite-in-wal-mode-and-support-postgres.md) | Default to SQLite in WAL mode, and support Postgres | Accepted |
| [ADR-06](0006-drop-ms-access-odbc-and-all-gprm-support.md) | Drop MS Access, ODBC, and all GPRM support | Accepted |
| [ADR-07](0007-keep-shared-password-roles-stored-hashed-in-the-database.md) | Keep shared-password roles, stored hashed in the database | Accepted |
| [ADR-08](0008-keep-polling-for-v1-and-add-push-later.md) | Keep polling for v1, and add push later | Superseded for kiosks by ADR-15 |
| [ADR-09](0009-build-the-manager-ui-with-django-templates-and-htmx.md) | Build the manager UI with Django templates, htmx, Alpine.js, Tailwind, and SortableJS | Accepted |
| [ADR-10](0010-rebuild-the-built-in-kiosk-pages-in-a-modern-front-end.md) | Rebuild the built-in kiosk pages in a modern front end | Accepted |
| [ADR-11](0011-leave-derby-timer-jar-and-the-browser-timer-unchanged.md) | Leave derby-timer.jar and the browser timer unchanged | Accepted |
| [ADR-12](0012-make-tests-id-agnostic-instead-of-copying-sqlite-rowid-reuse.md) | Make tests ID-agnostic instead of copying SQLite rowid reuse | Accepted |
| [ADR-13](0013-put-new-code-in-server-and-delete-website-at-cutover.md) | Put new code in `server/`, and delete `website/` at cutover | Accepted |
| [ADR-14](0014-ship-docker-images-as-the-only-deployment.md) | Ship Docker images as the only deployment | Accepted |
| [ADR-15](0015-push-kiosk-updates-for-sub-second-scene-switches.md) | Push kiosk updates for sub-second scene switches | Accepted |
| [ADR-16](0016-use-no-django-users-or-admin-roles-live-in-the-session.md) | Use no Django users or admin; roles live in the session | Accepted |

## Status lifecycle

Each ADR has one of these statuses:

- **Proposed.** The decision is written down, and the owner has not accepted it yet.
- **Accepted.** The owner accepted the decision. Later specs and code follow it.
- **Superseded by ADR-NN.** A later ADR replaced the decision. The old file stays in place, and its status links to the new ADR.

The owner answered every open question in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4) on 2026-10-21 and approved merging all 14 ADRs on 2026-10-22. An accepted ADR does not change its decision. To change an accepted decision, write a new ADR with the next free number, and set the old ADR's status to `Superseded by ADR-NN`. The new ADR names the ADR that it supersedes in its Context section.

## Owner decisions

This section copies the answers from [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4), dated 2026-10-21. Each answer names its owner decision (D1 to D12).

1. **How are events stored?** In one database (D2). The owner accepted option (b) of [#9](https://github.com/PorkChopExpress86/derbynet/issues/9), an `Event` row with a site-level active-event pointer and an `event` foreign key on every event-scoped model. The cross-event leak test uses identical rosters with a non-first event active, because the spike showed that a leak hides while the first event is active. One container holds every racing group of an event, and other events such as another pack's race day, one active at a time (D12).
2. **How do GPRM users bring their data in?** They do not. GPRM support is removed entirely, with no live sharing, no Access or ODBC, and no GPRM-specific import (D4). The generic CSV roster import stays. The importer still reads old DerbyNet SQLite event databases. ADR-06 records this.
3. **Which anonymous endpoints get permission checks?** All of them except the kiosk pages, standings, and the data those pages fetch (D5). `standings.php` becomes public. The replay page, the browser timer, check-in, photo upload, settings, and every action need a role. Every hole in the list this question named closes. When balloting is built, voters log in with the ballot password, as today. That rule is a default. ADR-07 and [#15](https://github.com/PorkChopExpress86/derbynet/issues/15) record the list.
4. **Must printables match pixel for pixel?** Printables are removed (D6). No printed documents ship. The FPDF documents, `render-document.php`, `print.php`, and barcode car tags go. On-screen reports and exports stay. [#55](https://github.com/PorkChopExpress86/derbynet/issues/55) is closed as not planned.
5. **Must external replay apps keep working?** No (D9). Only the browser replay page is supported, and the XML `replay-message` protocol is dropped. The remote WebRTC camera stays. The replay page requires a login, and camera setup happens while logged in. The operator picks the camera device, resolution, and frame rate with a live preview, and the replay station remembers the choice. Today `website/replay.php` asks for the window's size. The replay settings are playback speed (`replay-rate`), number of showings (`replay-num-showings`), seconds recorded before the last car finishes (`replay-skipback`), a new post-roll of seconds after the last car finishes, and a new maximum clip length that defaults to 10 s. The post-roll and the maximum length are a default that follows the owner's stated interpretation, and the owner can still correct it. [#53](https://github.com/PorkChopExpress86/derbynet/issues/53) carries this out.
6. **Must custom kiosk pages keep working?** No (D10). Kiosk pages are built in only, with no `local/kiosks/` loading. The Wrap-Up scene stops naming the missing `local/kiosks/feedback.kiosk`. The built-in pages are rebuilt in a modern front end chosen by [#71](https://github.com/PorkChopExpress86/derbynet/issues/71). ADR-10 records this.
7. **Is hosted multi-group mode in scope?** No (D11, confirmed in D12). One deployment serves one organization. `settings.lock` and the per-group config folders go. A container environment variable locks settings if that is ever needed.

The owner also decided these points in the same review.

- **Database (D1).** SQLite in WAL mode by default, Postgres as a first-class option through a compose profile and `DATABASE_URL`, and CI on both engines. ADR-05 records this.
- **Deployment (D3).** Docker is the only deployment, and the image runs Python 3.14 only. ADR-03 and ADR-14 record this. [#60](https://github.com/PorkChopExpress86/derbynet/issues/60) is closed as not planned. By default (D4), the Pi timer and photo-stand container images ([#59](https://github.com/PorkChopExpress86/derbynet/issues/59)) move to the backlog, and the browser timer is the supported timer client.
- **Balloting (D7).** [#54](https://github.com/PorkChopExpress86/derbynet/issues/54) moves to the backlog. If it is built later, a parent scans a QR code shown at check-in and the phone receives a unique per-racer voting token, with one vote per token and no device fingerprinting. The ballot rows S14 to S16 in [#64](https://github.com/PorkChopExpress86/derbynet/issues/64) leave the cutover gate with it.
- **Check-in and car weight (D6, D8).** A check-in station weighs the car, enters the racer and car names, generates the car number from the class, and takes the car photo in the browser. Each class sets its weigh-in to none, pass or fail, or recorded weight, in grams, ounces, or pounds. By default, weight is stored in grams, entered and shown in the class's unit, with an optional maximum that warns at check-in. [#70](https://github.com/PorkChopExpress86/derbynet/issues/70) builds it.
- **No barcode scanning (D13).** Check-in drops barcode scanning, both the USB scanner and the phone camera scanner. DerbyNet prints no `PWD` codes since printables went (D6). Volunteers find a racer by name or car number. The legacy `barcode` parameters stay for the photo stand's wire contract. [#42](https://github.com/PorkChopExpress86/derbynet/issues/42) records this.
- **Race-day flow (D11, D12).** The owner races one class at a time. Scouts round 1, then a scouts final with the top 4 or 8, then outlaws round 1, then an outlaws final. The playlist queues the whole day. Each step can wait for an operator before it starts, and by default the playlist waits between rounds while kiosks show the leaders. One tap starts the next round.
- **PHP fixes (D12).** The PHP server gets no fixes. It is never exposed to the internet, and every row in [#64](https://github.com/PorkChopExpress86/derbynet/issues/64) is fixed only in the Django port.

## Check the cited paths

`server/docs/adr/check-paths.sh` extracts every backticked repository path from the Markdown files in this directory and runs `test -e` on each one from the repository root. Paths with a `:LINE` suffix must also have at least that many lines. Run it from anywhere in the repository:

```sh
server/docs/adr/check-paths.sh
```

The script exits with a nonzero status and names each path that does not exist. The `ALLOWED_MISSING` list in the script holds paths that are intended but do not exist in the repository. The `NOT_PATHS` list holds backticked text that looks like a path but is not a path from the repository root, such as URL routes, query names, and files that an install creates.
