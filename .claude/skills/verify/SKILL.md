---
name: verify
description: Drive DerbyNet's web UI, PHP or the Django port, in a real browser to prove a change works, as a race crew would. Use after changing a page, an action.php handler, or a kiosk, and before reporting UI work as done.
---

# Verify DerbyNet

DerbyNet is a pinewood derby race manager served over HTTP: manager pages (setup, check-in, race dashboard, standings) and kiosk pages for TVs. Today the server is PHP (`website/`). The Django port under `server/` must pass the same journeys.

Drive it with the Playwright MCP browser tools (`mcp__plugin_playwright_playwright__browser_*`). When the session lists none, write a Python Playwright script and run it with `uv run --directory server python script.py`: `server/` installs Playwright and Chromium for its browser tests (`uv run playwright install chromium` once), and `server/ui/tests/browser/conftest.py` shows the login, the phone and desktop sizes, and the error listeners. Seed preconditions with the legacy API; drive the feature under test through the browser.

## Launch

`VERIFY_SERVER` picks the server: `php` (the default) or `django`. Verify a page or endpoint of the Django port with `VERIFY_SERVER=django`, the PHP behavior it copies with `php`. Pass the same variables to every helper of a run.

PHP runs from a regular clone (not a git worktree). Each run gets its own container, volume, and port:

```bash
VERIFY_RUN=v1 VERIFY_PORT=8090 .claude/skills/verify/bin/launch   # prints "ready http://127.0.0.1:8090"
VERIFY_RUN=v1 VERIFY_PORT=8090 .claude/skills/verify/bin/seed     # baseline below
```

`launch` builds the PHP image from this checkout with `testing/docker/run-suite.sh` (minutes the first time, seconds after) and refuses a name or port already in use. A fresh instance redirects every page to `setup.php` until `seed` creates the event.

`VERIFY_SERVER=django` runs `server/` from this checkout, a worktree too, with no Docker. Its database and settings live in `server/var/verify-<VERIFY_RUN>/`, and its log in the evidence folder as `server.log`. It runs with DEBUG off, as on race day, so `runserver --insecure` serves the static files. `seed` works the same, and `/login.php` redirects to the Django login page.

The Django server keeps the code and templates it started with. After each edit under `server/`, run `VERIFY_RUN=v1 VERIFY_PORT=8090 VERIFY_SERVER=django .claude/skills/verify/bin/restart` before driving again; the database, seed included, stays.

Baseline after `seed`: event `Verify`, 4 lanes, racers `Ava`..`Hal Verify` in dens `Lions` (cars 101-104) and `Tigers` (201-204), all checked in. Role passwords: RaceCoordinator `doyourbest`, RaceCrew `murphy`, Photo `flashbulb`.

Never drive `derbynet-local` on port 8080: that is the owner's own instance.

## Doctor

```bash
VERIFY_RUN=v1 VERIFY_PORT=8090 .claude/skills/verify/bin/doctor
```

Requires `doctor: ok`: the run's container (or runserver process) is running and owns port 8090, `index.php` answers, and it prints the checked-out commit. For Django it also checks that `static/ui/app.css` loads. Run it first whenever a page behaves unexpectedly.

## Drive

1. Log in through the UI: open `/login.php`, click the role button (`RaceCoordinator`), type the password in the textbox, press Enter. The page moves to `/index.php`.
2. Open the feature's page and use `browser_snapshot` refs for clicks. Stable handles are listed per feature in `features/`.
3. Accept the PHP `confirm()` and `alert()` dialogs with `browser_handle_dialog`.
4. Check phone layout with `browser_resize` to 390x844, desktop at 1280x800.
5. Headless background tabs are throttled: select a tab (`browser_tabs`) before acting in it, as with the fake timer beside the race dashboard.

Read [features/README.md](features/README.md) and the matching feature file before driving.

## Evidence

Save proof under `.claude/verify-evidence/<VERIFY_RUN>/` (git-ignored, kept by cleanup):

- A screenshot after the action: `browser_take_screenshot` with `filename: .claude/verify-evidence/v1/<feature>-<step>.png`.
- A second, read-only view of the stored state: the legacy API, as in `curl -s 'http://127.0.0.1:8090/action.php?query=racer.list' > .claude/verify-evidence/v1/racer-list-after.json`.
- Console errors: `browser_console_messages` with `level: error`. Any error fails the proof.

Exercise the real user path; the API is for seeding and for the second view only.

## Cleanup

```bash
VERIFY_RUN=v1 VERIFY_PORT=8090 .claude/skills/verify/bin/cleanup
```

Removes the run's container, volume, and images, or stops its Django server and deletes its database, and keeps `.claude/verify-evidence/v1/`. Close the browser with `browser_close`.
