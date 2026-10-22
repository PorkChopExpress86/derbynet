# ADR-05. Default to SQLite in WAL mode, and support Postgres

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Clients poll constantly. The timer heartbeat runs every 500 ms (`timer/src/org/jeffpiazza/derby/HttpTask.java`), replay every 250 ms (`website/replay.php:87`), the coordinator every 2 s (`website/js/coordinator-poll.js:751`), and each kiosk every 5 s (`website/js/kiosk-poller.js:45`). PHP uses no transactions.

The owner chose one database for all events after the [#9](https://github.com/PorkChopExpress86/derbynet/issues/9) prototype (owner decision D2).

## Decision

SQLite in WAL mode with a busy timeout is the default, as one file on the data volume. Postgres is a first-class option. It runs as a Postgres container that a compose profile starts, and `DATABASE_URL` selects it (owner decision D1). All events live in this one database. It has an `Event` row per event, a site-level active-event pointer, and an `event` foreign key on every event-scoped model (owner decision D2).

## Consequences

Wrap heat advance and result writing in `transaction.atomic()`. Model case-insensitive uniqueness with `Lower()` constraints, not `COLLATE NOCASE`. CI runs the model tests and the legacy shell suite on both engines. [#58](https://github.com/PorkChopExpress86/derbynet/issues/58) ships the compose profile.

## Evidence

Only the SQLite path works today. A single laptop at a race venue is the common install. The polling load above is small enough for SQLite once writes are short. The [#9](https://github.com/PorkChopExpress86/derbynet/issues/9) prototype switched events with 0 failed and 0 stale requests on both engines, and one database needs no migration work at switch time.
