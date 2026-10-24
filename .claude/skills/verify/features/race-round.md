# Race a round

The race coordinator schedules a den's round from the Race Dashboard, and heats run as the timer reports finishes. The fake timer page stands in for a real track timer.

## Sub-features

- `race-schedule` schedules a round (`Schedule Only` or `Schedule + Race`).
- `race-run` runs every heat with the fake timer in auto mode.
- `race-unschedule` removes a schedule before racing.

## How to get to it (user POV)

- Home page `Race Dashboard`, which is `/coordinator.php`.
- The timer side: `/fake-timer.php`, which a coordinator opens in a second tab.

## Driving it with Playwright MCP

Preconditions:

- Seeded baseline (4 lanes, Lions and Tigers checked in), logged in as RaceCoordinator.

- **Start the timer.** Open a second tab with `browser_tabs` action `new`, url `/fake-timer.php`. The Race Dashboard then shows `Timer Status` `CONNECTED`.
- **Open the round.** Select the dashboard tab and open `/coordinator.php`. Rounds are collapsed panels. Click the text `Lions, Round 1` to expand it.
- **Schedule.** Click `Schedule`. In the dialog, keep `1` for times per lane and click `Schedule + Race`.
- **Race.** Select the fake timer tab and tick the auto-mode checkbox (`#auto-mode-checkbox`). Heats run about 8 s apart.
- **Proof.** Poll `action.php?query=poll.coordinator` until the Lions round shows `heats_run` equal to `heats_scheduled` (4 heats for 4 racers on 4 lanes). Screenshot the dashboard, which lists the last heat's lane times.

## Gotchas

- Headless background tabs are throttled. A click in the background dashboard tab can time out, so select the tab first.
- `Schedule` appears only when the lane count is set and the round has unscheduled racers.
- The fake timer reports only while its tab is open and racing mode is on. `Schedule + Race` turns racing on.
