# ADR-07. Keep shared-password roles, stored hashed in the database

## Status

Accepted on 2026-10-22, when the owner approved merging the ADR pull request. The owner answered the open questions on 2026-10-21 in [issue #4](https://github.com/PorkChopExpress86/derbynet/issues/4).

## Context

Events run today with one shared password per role. The alternative was per-person user accounts. It was deferred because volunteers change every year, and shared role passwords are how events run today.

## Decision

Keep role logins with one shared password per role. Store roles in the database with hashed passwords. Keep the permission bit values from `website/inc/permissions.inc`, from `VIEW_RACE_RESULTS_PERMISSION` (1) to `ADMINISTRATION_PERMISSION` (65536).

## Consequences

A command imports `config-roles.inc`. New installs seed the same default passwords, so the docs and tests stay true. An empty password stays valid for `Timer`. When `config-roles.inc` is missing, `website/inc/roles.inc` grants the anonymous role permissions `-1`. Django does not copy that fallback. There are no per-person accounts in v1. [#15](https://github.com/PorkChopExpress86/derbynet/issues/15) owns the details.

**Anonymous access (owner decision D5).** A visitor with no login reaches only the kiosk pages and standings, plus the data those pages fetch. That is `kiosk.php`, `poll.kiosk`, the `poll` values the now-racing and on-deck pages show, `poll.results`, `award.current`, `slide.next`, and the racer photos, slides, and images that kiosks display. `standings.php` becomes public. Today it requires `VIEW_RACE_RESULTS`. Every other page and action requires a logged-in role. That includes the replay page, the remote camera, the browser timer (the `Timer` role, as today), check-in, photo upload, and settings. Every hole listed in the owner's answer to question 3 closes. When balloting is built ([#54](https://github.com/PorkChopExpress86/derbynet/issues/54), in the backlog), a voter logs in with the ballot password, as today. That last rule is a default the owner can change.

## Evidence

`local/config-roles.inc` holds plaintext passwords that `website/ajax/action.role.login.inc` compares loosely. The jar logs in as `Timer` with an empty password and reads the `timer-message` value (4096) from `query=role.list`. `testing/default.passwords` and the docs list the defaults written by `website/inc/write-config-files.inc`.
