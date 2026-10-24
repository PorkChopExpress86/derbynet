# Event setup

A race coordinator creates a new event (its own database and photo folders) or opens an existing one, then sets the number of lanes on the track.

## Sub-features

- `setup-create` creates a new event from the Choose Database dialog.
- `setup-lanes` sets the lane count on the Settings page.

## How to get to it (user POV)

- A fresh install sends every page to `/setup.php`.
- Home page `Set-Up` button, then `Choose Database`.
- Home page `Settings`, field "Number of lanes on the track".

## Driving it with Playwright MCP

Preconditions:

- A launched instance. For `setup-create`, skip `bin/seed` so the install is fresh.

- **Open dialog.** Go to `/setup.php`, click `Choose Database`. A dialog with a text field (`#ez_database_name`) appears.
- **Create event.** Type `Pinewood Test` and click the dialog's `Submit`. `/setup.php` then shows `SQLITE: /var/lib/derbynet/<year>/Pinewood Test/derbynet.sqlite3` and `Schema version is S1010, as expected.`
- **Set lanes.** Log in as RaceCoordinator, open `/settings.php`, fill `#n-lanes` with `4`, and leave the field so it fires `change`. The page saves without a button.
- **Proof.** Screenshot `/setup.php`, and read `action.php?query=poll.coordinator` for `"lanes": 4` in `timer-state`.

## Gotchas

- `action.php` answers 302 until a database exists. A session gets setup rights only after it opens `/setup.php`.
- Settings save on `change`, about 1 s later. Wait before navigating away.
- A second `Choose Database` with an existing name opens that event and does not create a new one.
