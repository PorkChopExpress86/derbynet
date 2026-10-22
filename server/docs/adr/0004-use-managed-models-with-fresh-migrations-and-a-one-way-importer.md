# ADR-04. Use managed models with fresh migrations and a one-way importer

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

The schema is GPRM compatible, at version 10 (`website/inc/schema_version.inc`). No table declares a foreign key. `RaceInfo` is a key-value table that holds settings and live race state. 40 PHP files call `schema_version()`, most of them to branch on older database versions.

The alternative was unmanaged models on the GPRM schema. It was rejected because it keeps tables without primary keys and the version branches spread across 40 files.

## Decision

Design clean Django models with their own migrations. Do not map unmanaged models onto the legacy tables. A management command imports legacy SQLite files at schema versions 1 to 10, and snapshot files.

## Consequences

No `schema_version()` branches survive. The `RaceInfo` key names survive only as the importer's mapping table and as the vocabulary of `.pref` preference files, which users keep from year to year. [#12](https://github.com/PorkChopExpress86/derbynet/issues/12) and [#14](https://github.com/PorkChopExpress86/derbynet/issues/14) carry this out.

## Evidence

No table declares a foreign key. `Kiosks`, `ConstituentClasses`, and `BallotAwards` have no primary key. `Classes.constituents` and `Classes.rankids` duplicate `ConstituentClasses` as comma-separated text. Upgrades today run by hand through `action=database.execute&script=update-schema`.
