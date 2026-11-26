# Timer test and timer status

The race coordinator simulates racing from the timer test page, so cars run down the track without a schedule. The timer status page and the Race Dashboard show while a test runs. During a heat intermission (Django only, D12) a timer test leaves racing off and the intermission open (D145).

## Sub-features

- `timer-test-mode` turns the timer test page's Simulate racing switch on and off.
- `timer-status-test` is the timer status banner that says simulated racing is on.
- `timer-test-intermission` runs a timer test while the intermission is open.

## How to get to it (user POV)

- Django: the Race Dashboard's `Test` link (`/timer/test/`, desktop width) and the timer status page `/timer/`, which the dashboard's `Timer status` link opens at phone width. Neither is in the menu.
- PHP: `/timer-test.php`, one page with the timer status, the switch and the lanes.

## Driving it with Playwright MCP

Preconditions:

- Seeded baseline, logged in as RaceCoordinator. For `timer-test-intermission`, seed with `bin/seed --intermission` instead. On Django it ends `intermission: Lions, Round 2`, and the dashboard's `[data-intermission]` card reads `Up next: Lions, Round 2`. On PHP it races the same Lions Round 1 and ends `intermission: none (PHP has none)`, with the final scheduled and racing off.

- **Simulate racing on (Django).** Open `/timer/test/`. In `#timer-test-live`, click the switch named `Simulate racing` (`role=switch`). It turns `aria-checked="true"` and a toast says `Simulated racing on.`
- **The timer status banner (Django).** Open `/timer/`. `p[data-test-running]` reads `Simulated racing is on.` and links to `Timer test`. With no test it is absent.
- **The dashboard (Django).** `/dashboard/` shows `[data-herald]` `Simulated racing in progress` with a `Stop` button (`data-action="stop-test"`), above the intermission card when one is open.
- **Simulate racing off (Django).** Back on `/timer/test/`, click the switch again: `aria-checked="false"`, the banner and the herald are gone, and the intermission card stays.
- **PHP.** On `/timer-test.php` the switch is `#test-mode`, a hidden checkbox inside `div.flipswitch`: click `div.flipswitch:has(#test-mode)` and check `#test-mode` is checked. The timer status is `#timer_status_text`.
- **Proof.** `action.php?query=poll.coordinator`: a running test has `current-heat.roundid` -100. On Django during the intermission `now_racing` stays false; on PHP the test turns racing on. `action.php?query=poll&values=intermission` names the open intermission on Django and answers `{}` on PHP.

## Gotchas

- `seed --intermission` makes the final's step wait through the Django playlist page's `Wait for operator` switch (`/playlist/step/<id>/waits/`), since the legacy API never makes a step wait (D12). It skips that post on PHP.
- The seed's timer messages come from curl, so the timer shows `NOT CONNECTED` a few seconds later. The timer test still runs; post `action=timer-message&message=HEARTBEAT` with the seed's `timer-cookies.txt` jar to connect it again.
