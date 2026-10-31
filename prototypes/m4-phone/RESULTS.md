## Phone prototypes: race dashboard and check-in

Static prototypes for the "Prototype and decision" section, on branch `spike-39-phone-prototypes` in `prototypes/m4-phone/` (not for merging, and nothing under `server/ui/`). Pages are Django templates rendered by the standalone template engine, with htmx 2.0.4, Alpine.js 3.14.8, and Tailwind 4.1.11 from the standalone CLI. The data is fixtures only: a 4-lane track, 40 racers in 3 dens (Tigers 14, Wolves 13, Bears 13), Wolves round 1 racing at heat 6 of 11, and heat 6 running with lane 3 empty. The dashboard shows the RaceCoordinator bottom nav (Dashboard, Check-In, Standings, More), and check-in shows the RaceCrew nav (Check-In, Photos, On Deck, More).

The layout choice is the owner's. This comment only records what was built and measured.

### Method

- Playwright Chromium, viewport 390×844 CSS px, device scale 2 (PNG 780×1688), touch on, `prefers-reduced-motion: reduce` (so sheets are captured fully open), `prefers-color-scheme` light and dark.
- The sheet state is opened by tapping the real control (for example, the round's ⋮ button, or a racer row), then measured and scanned in that state.
- **axe-core 4.10.3**, tags wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa, and best-practice, run on the whole document in each state.
- **Tap targets**: every link, button, input, select, switch, and tab that is rendered and not inside `inert` content (the page behind an open sheet is `inert`). A radio inside a `<label>` is measured by its label. The visually hidden skip link is excluded. The smallest is by shorter side. The spec's floor is 44×44.
- Polled sections use `hx-trigger="every Ns [...]"` with the PHP intervals (2 s dashboard, 10 s check-in). Polling pauses while a sheet is open or an input has focus, and the prototype server answers each poll with 204, the spec's "nothing changed" reply.

### Summary

- axe violations: **0** across all 24 states.
- Smallest tap target in any state: **44 px** on its shorter side. Targets under 44 px: **0**.
- axe "needs review" items: skip-link. `skip-link` is reported in each sheet state, because the skip link's target (`#main`) is inside the inert page while a sheet is open. That is expected.
- Console or page errors: 0. Focus moved into the open sheet in every sheet state.
- Lane colors keep their hue in both themes. Badge contrast: lane 1 white on #dc2626 4.83:1, lane 2 white on #2563eb 5.17:1, lane 3 white on #15803d 5.02:1, lane 4 black on #facc15 13.71:1.

### Screenshots

| Layout | Light, default | Light, sheet open | Dark, default | Dark, sheet open |
|---|---|---|---|---|
| **Dashboard A: one column, sticky current-heat card**<br>sheet: round actions | <img src="screenshots/dashboard-a-light-default.png" width="195" alt="dashboard-a light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 14 | <img src="screenshots/dashboard-a-light-sheet.png" width="195" alt="dashboard-a light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 7 | <img src="screenshots/dashboard-a-dark-default.png" width="195" alt="dashboard-a dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 14 | <img src="screenshots/dashboard-a-dark-sheet.png" width="195" alt="dashboard-a dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 7 |
| **Dashboard B: tabs (Now, Rounds, Timer), racing switch in the app bar**<br>sheet: new-round stepper | <img src="screenshots/dashboard-b-light-default.png" width="195" alt="dashboard-b light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 12 | <img src="screenshots/dashboard-b-light-sheet.png" width="195" alt="dashboard-b light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 | <img src="screenshots/dashboard-b-dark-default.png" width="195" alt="dashboard-b dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 12 | <img src="screenshots/dashboard-b-dark-sheet.png" width="195" alt="dashboard-b dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 |
| **Dashboard C: docked heat-control sheet over the round list**<br>sheet: expanded heat sheet | <img src="screenshots/dashboard-c-light-default.png" width="195" alt="dashboard-c light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 14 | <img src="screenshots/dashboard-c-light-sheet.png" width="195" alt="dashboard-c light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 6 | <img src="screenshots/dashboard-c-dark-default.png" width="195" alt="dashboard-c dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 14 | <img src="screenshots/dashboard-c-dark-sheet.png" width="195" alt="dashboard-c dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 6 |
| **Check-in A: card list, pass switch on each card**<br>sheet: racer details | <img src="screenshots/checkin-a-light-default.png" width="195" alt="checkin-a light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 90 | <img src="screenshots/checkin-a-light-sheet.png" width="195" alt="checkin-a light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 | <img src="screenshots/checkin-a-dark-default.png" width="195" alt="checkin-a dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 90 | <img src="screenshots/checkin-a-dark-sheet.png" width="195" alt="checkin-a dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 |
| **Check-in B: scan first, large search and camera button**<br>sheet: found racer | <img src="screenshots/checkin-b-light-default.png" width="195" alt="checkin-b light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 22 | <img src="screenshots/checkin-b-light-sheet.png" width="195" alt="checkin-b light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 5 | <img src="screenshots/checkin-b-dark-default.png" width="195" alt="checkin-b dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 22 | <img src="screenshots/checkin-b-dark-sheet.png" width="195" alt="checkin-b dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 5 |
| **Check-in C: grouped by den, sticky group headers**<br>sheet: racer details | <img src="screenshots/checkin-c-light-default.png" width="195" alt="checkin-c light default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 86 | <img src="screenshots/checkin-c-light-sheet.png" width="195" alt="checkin-c light sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 | <img src="screenshots/checkin-c-dark-default.png" width="195" alt="checkin-c dark default"><br>axe: 0 violations<br>smallest target: 44×44 px (Log out)<br>under 44 px: 0 of 86 | <img src="screenshots/checkin-c-dark-sheet.png" width="195" alt="checkin-c dark sheetState"><br>axe: 0 violations<br>smallest target: 44×44 px (Close)<br>under 44 px: 0 of 9 |

### Space at 390×844 (light, scrolled to top)

| Layout | Fixed or sticky chrome | Left for scrolling content | Racer rows fully visible |
|---|---|---|---|
| Dashboard A | 487 px | 357 px | n/a |
| Dashboard B | 190 px | 654 px | n/a |
| Dashboard C | 276 px | 568 px | n/a |
| Check-in A | 278 px | 566 px | 9 |
| Check-in B | 121 px | 723 px | 4 |
| Check-in C | 186 px | 658 px | 10 |

Chrome is the app bar, the bottom nav, and the layout's own sticky or docked part. In check-in C the 40 px den header also sticks once the list scrolls.

### Trade-offs

**Dashboard.** A keeps the current heat, the racing switch, the heat buttons, and the timer and replay status on screen at all times, and anything else scrolls under it. The cost is height: the sticky card plus app bar and nav take 487px of 844, which leaves 357px for last-heat results and the round cards. B gives the scrolling content the most room (654px) and puts the racing switch in the app bar, where it stays reachable from every tab. But the app bar is full at 390px, and the round list and timer detail are one tap away behind a tab, so the current heat and the round cards are never on screen together. C shows the round list with the heat schedule expanded, the current heat marked, and a compact control dock (155px) with car numbers, the racing switch, Re-run, and Skip. Racer names, Previous, manual results, the status pills, and the last results are one tap away in the expanded sheet. Its dock sits above the bottom nav, so the bottom of the screen has two stacked control areas.

**Check-in.** A shows every racer with a den filter and a running count. Each pass is one tap on the card's switch, and 9 racer rows fit at the top of the list under a 157px filter bar. B leads with the search field and camera button for scanner or tag workflows. Only 4 rows of "Not passed yet" are visible without scrolling, and the main action (Pass inspection) happens in a sheet after a scan or tap, which adds a step for a manual pass but gives a large confirm target. C fits the most rows (10), because the filter bar is just the search field, and the sticky den header carries each den's own count ("10 of 13 passed"). It drops the den filter: a den is reached by scrolling, not by a tap.

### Limits of these numbers

- axe checks what it can compute. It does not judge focus order, screen reader wording, or the contrast of a switch thumb against its track. The dark theme's green "on" track with a white thumb is a low-contrast pair, and the state is also shown by the thumb's position.
- The search fields, the den filter, Previous, Re-run, Skip, and Save are visual only. Switches, tabs, sheets, the stepper, and the radio cards work.
- Screenshots use Chromium only. iOS Safari (safe-area insets, `dvh`, focus zoom) was not tested.
- Rebuild: `prototypes/m4-phone/build.sh --shoot` (needs uv, Node, and Playwright with Chromium).
