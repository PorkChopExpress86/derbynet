# ADR-01. Build in parallel and cut over once

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

PHP uses no transactions. `write_raceinfo` reads, then inserts, and `RaceInfo.itemkey` has no unique index. The schema is GPRM compatible, at version 10 (`website/inc/schema_version.inc`), and no table declares a foreign key.

The alternative was a shared database during a staged migration. It was rejected because it needs unmanaged models on a schema without keys, and two writers without transactions.

## Decision

Build the Django server next to the PHP server. The two never share a database. One release switches users over and removes PHP.

## Consequences

PHP stays the production server until cutover. Data moves through the importer ([#14](https://github.com/PorkChopExpress86/derbynet/issues/14)). CI runs the same suite against both servers.

## Evidence

A second writer on the same file would race with the PHP `RaceInfo` writes. Managed Django models change the schema, which PHP cannot read.
