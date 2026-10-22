# Architecture decisions for the Django port

These architecture decision records (ADRs) record how the DerbyNet server moves from PHP to Django. The source spec is [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4). Later specs and tickets build on these decisions and cite them by number.

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-01](0001-build-in-parallel-and-cut-over-once.md) | Build in parallel and cut over once | Proposed |
| [ADR-02](0002-keep-the-legacy-wire-contract-permanently.md) | Keep the legacy wire contract permanently | Proposed |
| [ADR-03](0003-use-django-5-2-lts-on-python-3-11-or-later.md) | Use Django 5.2 LTS on Python 3.11 or later | Proposed |
| [ADR-04](0004-use-managed-models-with-fresh-migrations-and-a-one-way-importer.md) | Use managed models with fresh migrations and a one-way importer | Proposed |
| [ADR-05](0005-default-to-sqlite-in-wal-mode-and-support-postgres.md) | Default to SQLite in WAL mode, and support Postgres | Proposed |
| [ADR-06](0006-drop-ms-access-odbc-and-live-gprm-sharing.md) | Drop MS Access, ODBC, and live GPRM sharing | Proposed |
| [ADR-07](0007-keep-shared-password-roles-stored-hashed-in-the-database.md) | Keep shared-password roles, stored hashed in the database | Proposed |
| [ADR-08](0008-keep-polling-for-v1-and-add-push-later.md) | Keep polling for v1, and add push later | Proposed |
| [ADR-09](0009-build-the-manager-ui-with-django-templates-and-htmx.md) | Build the manager UI with Django templates, htmx, Alpine.js, Tailwind, and SortableJS | Proposed |
| [ADR-10](0010-keep-the-kiosk-tv-pages-as-they-look-today.md) | Keep the kiosk TV pages as they look today | Proposed |
| [ADR-11](0011-leave-derby-timer-jar-and-the-browser-timer-unchanged.md) | Leave derby-timer.jar and the browser timer unchanged | Proposed |
| [ADR-12](0012-make-tests-id-agnostic-instead-of-copying-sqlite-rowid-reuse.md) | Make tests ID-agnostic instead of copying SQLite rowid reuse | Proposed |
| [ADR-13](0013-put-new-code-in-server-and-delete-website-at-cutover.md) | Put new code in `server/`, and delete `website/` at cutover | Proposed |
| [ADR-14](0014-make-docker-and-debian-or-pi-first-class.md) | Make Docker and Debian or Pi first-class, and serve Mac and Windows through Docker Desktop | Proposed |

## Status lifecycle

Each ADR has one of these statuses:

- **Proposed.** The decision is written down, and the owner has not accepted it yet.
- **Accepted.** The owner accepted the decision. Later specs and code follow it.
- **Superseded by ADR-NN.** A later ADR replaced the decision. The old file stays in place, and its status links to the new ADR.

The owner accepts an ADR by merging the pull request that adds it and then committing a change that sets its status to Accepted. An accepted ADR does not change its decision. To change an accepted decision, write a new ADR with the next free number, and set the old ADR's status to `Superseded by ADR-NN`. The new ADR names the ADR that it supersedes in its Context section.

## Open questions for the owner

The owner answers each question, or accepts its recommended default, in a comment on [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

1. **How are events stored?** [#9](https://github.com/PorkChopExpress86/derbynet/issues/9) answers this with a prototype. The recommended default is the outcome of that spec, which provisionally favors an `Event` row on every event-scoped model.
2. **How do GPRM users bring their data in?** The recommended default is a CSV export of the roster from GPRM, loaded through the roster import. Add a Windows-only `pyodbc` import command later only if users ask.
3. **Which anonymous endpoints get permission checks?** The recommended default is to close every hole that no field client needs. That covers `session.write`, `snapshot.get`, `settings.list` secrets, `file.directory.nodata`, `file.stat`, `photo.php/info`, `render-document.php`, `video.upload`, the `PRESENT_AWARDS` grant in `kiosk.php`, and `phpinfo()` on `about.php`. Keep anonymous access where kiosks, the jar, and the photo stand need it. [#15](https://github.com/PorkChopExpress86/derbynet/issues/15) holds the final list.
4. **Must printables match pixel for pixel?** The recommended default is no. Match the layout so Avery labels line up within 0.5 mm, checked by printing each document once. Names in any script then render correctly.
5. **Must external replay apps keep working?** `docs/Developers- Replay Messages.fodt` describes an XML `replay-message` action that the code no longer serves. The recommended default is to support only the JSON `replay.message` protocol that `website/replay.php` uses.
6. **Must custom kiosk pages keep working?** Users may have PHP pages in `local/kiosks/`, and the seeded Wrap-Up scene in `website/sql/sqlite/scene-tables.inc` names `local/kiosks/feedback.kiosk`. The recommended default is no PHP. Load Django templates from a configured kiosk folder, and have the importer flag scenes that name a missing page.
7. **Is hosted multi-group mode in scope?** Today it uses `settings.lock` and one config folder per group. The recommended default is no. Run one deployment per group, and replace `settings.lock` with an environment variable.

## Check the cited paths

`server/docs/adr/check-paths.sh` extracts every backticked repository path from the Markdown files in this directory and runs `test -e` on each one from the repository root. Paths with a `:LINE` suffix must also have at least that many lines. Run it from anywhere in the repository:

```sh
server/docs/adr/check-paths.sh
```

The script exits with a nonzero status and names each path that does not exist. The `ALLOWED_MISSING` list in the script holds paths that are intended but do not exist in the repository. The `NOT_PATHS` list holds backticked text that looks like a path but is not a path from the repository root, such as URL routes and files that an install creates.
