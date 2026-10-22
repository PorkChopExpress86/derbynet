# ADR-13. Put new code in `server/`, and delete `website/` at cutover

## Status

Accepted. The owner accepted it on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

ADR-01 builds the Django server next to the PHP server until one cutover release. Both code bases live in this repository during that time.

## Decision

The Django project lives in a new top-level `server/` directory. `website/` stays untouched until the cutover release deletes it.

## Consequences

Golden fixtures live in `server/tests/golden/`. `timer/`, `extras/`, and `docs/` stay where they are. [#62](https://github.com/PorkChopExpress86/derbynet/issues/62) deletes `website/` and the PHP-only installer files.

## Evidence

`website/` is the PHP document root, and Ant writes generated PHP into `website/inc/`.
