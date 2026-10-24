# Results and standings

After heats run, `Results By Racer` shows each racer's time in every lane, and `Standings` ranks racers by average time, overall and per den.

## Sub-features

- `results-lanes` shows per-lane times with photos.
- `standings-rank` ranks racers with place, average, best, and worst.
- `standings-public` is open to anonymous visitors (owner decision D5, for the Django port).

## How to get to it (user POV)

- Home page `Results By Racer` (`/racer-results.php`) and `Standings` (`/standings.php`).

## Driving it with Playwright MCP

Preconditions:

- `race-round` completed for the Lions.

- **Results.** Open `/racer-results.php`. A `Lions, Round 1` table has one row per racer and columns `Lane 1` to `Lane 4`.
- **Standings.** Open `/standings.php`. The selector reads `Pack`. Rows list Place, Car Number, Name, Den, Heats, Average, Best, Worst.
- **Proof.** Check one racer's Average against the mean of their four lane times on the results page. They must agree to 3 decimals. Screenshot both pages at 1280x800 and 390x844.

## Gotchas

- PHP still requires a login for `/standings.php` and redirects anonymous visitors to `/login.php`. The Django port makes it public. Record which behavior you saw.
- Times come from the fake timer and are random, so assert relationships (average equals the mean, place order follows average), not values.
