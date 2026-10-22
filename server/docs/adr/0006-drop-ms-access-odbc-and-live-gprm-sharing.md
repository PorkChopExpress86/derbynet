# ADR-06. Drop MS Access, ODBC, and live GPRM sharing

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The PHP server can open GPRM and MS Access databases through ODBC, and `docs/Sharing a Database With GPRM.fodt` describes sharing one database file with GPRM. ADR-01 gives the Django server its own managed schema, which GPRM cannot read.

## Decision

The Django server reads and writes only its own database. It does not open GPRM or Access files.

## Consequences

The Windows one-click build with ODBC is retired. `docs/Sharing a Database With GPRM.fodt` is retired. Open question 2 in the [ADR index](README.md#open-questions-for-the-owner) covers how GPRM users bring data in.

## Evidence

The Access path is broken today. `website/sql/access/update-schema.inc:61` subtracts instead of assigning, so the Partitions table is never created. The Access schema lacks the columns of versions 5 to 10. `testing/exercise-one-database.sh` skips the snapshot restore in `access` mode.
