# ADR-12. Make tests ID-agnostic instead of copying SQLite rowid reuse

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The shell suite in `testing/` talks only HTTP, so it can gate both servers once it stops scraping HTML. That work is in [#6](https://github.com/PorkChopExpress86/derbynet/issues/6). Some tests assume the ID values that SQLite assigns.

## Decision

Tests look IDs up by name or take them from create responses. Django does not imitate SQLite ID reuse.

## Consequences

[#6](https://github.com/PorkChopExpress86/derbynet/issues/6) removes literal IDs. Clients that detect new rounds and results through the highest ID keep working, because IDs only grow.

## Evidence

`website/sql/sqlite/schema.inc` declares `INTEGER PRIMARY KEY` without `AUTOINCREMENT`, so SQLite reuses a deleted maximum ID. `testing/test-aggregate-rounds.sh` expects `roundid=6` to be reused. Django's SQLite backend emits `AUTOINCREMENT`.
