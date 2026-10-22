# ADR-05. Default to SQLite in WAL mode, and support Postgres

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Clients poll constantly. The timer heartbeat runs every 500 ms (`timer/src/org/jeffpiazza/derby/HttpTask.java`), replay every 250 ms (`website/replay.php:87`), the coordinator every 2 s (`website/js/coordinator-poll.js:751`), and each kiosk every 5 s (`website/js/kiosk-poller.js:45`). PHP uses no transactions.

## Decision

SQLite in WAL mode with a busy timeout is the default. Postgres is supported and tested.

## Consequences

Wrap heat advance and result writing in `transaction.atomic()`. Model case-insensitive uniqueness with `Lower()` constraints, not `COLLATE NOCASE`. Run the model tests on both engines in CI.

## Evidence

Only the SQLite path works today. A single laptop at a race venue is the common install. The polling load above is small enough for SQLite once writes are short.
