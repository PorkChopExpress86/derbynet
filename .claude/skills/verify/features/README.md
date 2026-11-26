# DerbyNet verification map

The maintained source for verifying DerbyNet's user-facing behavior. Read this index, then use the matching feature file as the recipe.

## Baseline preconditions

- An instance from `bin/launch`, seeded with `bin/seed`, and `bin/doctor` reporting `doctor: ok`.
- Logged in as RaceCoordinator through `/login.php`, unless a feature says otherwise.
- Never drive an instance this run did not start.

## Driving conventions

- Prefer accessible names and button values (`Schedule + Race`, `RaceCoordinator`) over CSS selectors.
- Many controls sit inside collapsed panels or modals. Open the panel, take a new snapshot, then click.
- Restore the baseline after a mutation, or relaunch, before the next feature.

## Proof and skip reporting

- Capture the action and the resulting state: a screenshot and a second read-only view (an API query or another page).
- Record the feature ID and entry point with every artifact.
- Report an unreachable path with the attempted step and the unmet precondition. A skipped entry point is not verified through another path.

## Features

- [Event setup](./event-setup.md) creates or opens an event and sets the lane count.
- [Check-in](./checkin.md) passes and un-passes racers, adds and edits racers.
- [Race a round](./race-round.md) schedules a round and races it with the fake timer.
- [Results and standings](./results-standings.md) shows lane times and the ranked standings.
- [Kiosk display](./kiosk.md) shows a scene on an anonymous kiosk TV.
- [Timer test and timer status](./timer.md) simulates racing, also during a heat intermission.

## Proven so far

- `checkin-unpass` ran end to end with this skill on 2026-10-24 (Playwright MCP, PHP server at 0e16e261).
- `setup-create`, `setup-lanes`, `race-schedule`, `race-run`, `results-lanes`, `standings-rank`, and `kiosk-idle` ran on 2026-10-24 through an earlier Puppeteer script, not yet through this skill.
- `timer-test-mode`, `timer-status-test`, and `timer-test-intermission` ran on 2026-11-26 through a Python Playwright script that clicks and checks the handles `timer.md` names, step for step, not through the Playwright MCP tools (#537): Django during an intermission from `seed --intermission`, and the switch on PHP.
- `checkin-pass`, `checkin-new`, `checkin-edit`, `race-unschedule`, `standings-public`, and `kiosk-scene` are written from the page source and have not been driven yet. Treat their handles as unverified until a run confirms them.
