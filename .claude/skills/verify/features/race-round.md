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

## The timer under `VERIFY_SERVER=django`

Django has no `/fake-timer.php` until #50, and the Race Dashboard is `/dashboard/` (`coordinator.php` redirects there). Play the timer with curl, as `run_heat` in `testing/common.sh` does, in a cookie jar of its own:

```bash
T=.claude/verify-evidence/v1/timer-cookies.txt
B=http://127.0.0.1:8090/action.php
curl -s -c $T -b $T $B --data 'action=role.login&name=Timer&password='
curl -s -c $T -b $T $B --data 'action=timer-message&message=HELLO'
curl -s -c $T -b $T $B --data 'action=timer-message&message=STARTED'
curl -s -c $T -b $T $B --data 'action=timer-message&message=FINISHED&lane1=2.111&lane2=2.222&lane3=2.333&lane4=2.444'
```

Each FINISHED records the current heat and answers `heat-ready` for the next. The page's stable handles (`[data-heat-text]`, `[data-lane="1"] [data-result]`, `[data-holding]`) are the ones `server/ui/tests/browser/test_dashboard.py` drives.

## Gotchas

- Headless background tabs are throttled. A click in the background dashboard tab can time out, so select the tab first.
- `Schedule` appears only when the lane count is set and the round has unscheduled racers.
- The fake timer reports only while its tab is open and racing mode is on. `Schedule + Race` turns racing on.
