# Check-in

The check-in crew marks racers as passed inspection (or reverses it), adds walk-in racers, and edits a racer's details. Only passed racers get scheduled.

## Sub-features

- `checkin-pass` passes a racer with the Passed switch.
- `checkin-unpass` reverses a pass after a confirm dialog.
- `checkin-new` adds a racer with `New Racer`.
- `checkin-edit` edits a racer with `Change`.

## How to get to it (user POV)

- Home page `Race Check-In`, which is `/checkin.php`.
- Needs a logged-in RaceCrew or RaceCoordinator.

## Driving it with Playwright MCP

Preconditions:

- Seeded baseline: all 8 racers passed.

- **Find the row.** Open `/checkin.php` and snapshot. Each row has cells for den, car number, last name, and first name, plus a `Yes No` switch.
- **Un-pass.** Click the switch in car `104`'s row. A confirm dialog reads `Are you sure you want to unregister Dee Verify?`. Accept it with `browser_handle_dialog`.
- **Pass.** Click the same switch again. No dialog appears.
- **New racer.** Click `New Racer`, fill first name, last name, car number, and den in the dialog, and submit. A new row appears.
- **Proof.** Screenshot the table, then read `action.php?query=racer.list` and check that car `104` has `"passed": false` (or `true` after re-passing).

## Gotchas

- Un-passing needs the REVERT_CHECK_IN permission. RaceCrew may get a refusal where RaceCoordinator does not.
- A racer with heats already run can't be un-passed. The baseline has no heats, so run this before `race-round`.
- The phone layout is the desktop table scaled down. Check it at 390x844 when changing this page.
