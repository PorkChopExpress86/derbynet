# ADR-06. Drop MS Access, ODBC, and all GPRM support

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The PHP server can open GPRM and MS Access databases through ODBC, and `docs/Sharing a Database With GPRM.fodt` describes sharing one database file with GPRM. ADR-01 gives the Django server its own managed schema.

The owner removed all GPRM support on 2026-10-21 (owner decision D4).

## Decision

The Django server reads and writes only its own database. It does not open GPRM or Access files, does not share a database with GPRM, and has no GPRM-specific import (owner decision D4). The generic CSV roster import stays as an ordinary feature. The importer still brings in old DerbyNet SQLite event databases.

## Consequences

The Windows one-click build with ODBC is retired. `docs/Sharing a Database With GPRM.fodt` is retired with no replacement page. A GPRM user who wants to move can export a roster as CSV and load it through the roster import, like any other CSV.

## Evidence

The Access path is broken today. `website/sql/access/update-schema.inc:61` subtracts instead of assigning, so the Partitions table is never created. The Access schema lacks the columns of versions 5 to 10. `testing/exercise-one-database.sh` skips the snapshot restore in `access` mode.
