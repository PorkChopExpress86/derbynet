# ADR-07. Keep shared-password roles, stored hashed in the database

## Status

Proposed. The owner accepts it in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Events run today with one shared password per role. The alternative was per-person user accounts. It was deferred because volunteers change every year, and shared role passwords are how events run today.

## Decision

Keep role logins with one shared password per role. Store roles in the database with hashed passwords. Keep the permission bit values from `website/inc/permissions.inc`, from `VIEW_RACE_RESULTS_PERMISSION` (1) to `ADMINISTRATION_PERMISSION` (65536).

## Consequences

A command imports `config-roles.inc`. New installs seed the same default passwords, so the docs and tests stay true. An empty password stays valid for `Timer`. When `config-roles.inc` is missing, `website/inc/roles.inc` grants the anonymous role permissions `-1`. Django does not copy that fallback. There are no per-person accounts in v1. [#15](https://github.com/PorkChopExpress86/derbynet/issues/15) owns the details.

## Evidence

`local/config-roles.inc` holds plaintext passwords that `website/ajax/action.role.login.inc` compares loosely. The jar logs in as `Timer` with an empty password and reads the `timer-message` value (4096) from `query=role.list`. `testing/default.passwords` and the docs list the defaults written by `website/inc/write-config-files.inc`.
