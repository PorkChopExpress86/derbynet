# Kiosk display

A TV browser opens the kiosk page with no login, and the coordinator chooses what each kiosk shows (Now Racing, Standings, Awards, slides) from the Kiosk Dashboard.

## Sub-features

- `kiosk-idle` shows the identify screen with the kiosk's name and address.
- `kiosk-scene` switches to the scene the coordinator picks.

## How to get to it (user POV)

- The TV opens `/kiosk.php` anonymously. It adds `?address=` itself.
- The coordinator uses `Kiosk Dashboard` (`/kiosk-dashboard.php`).

## Driving it with Playwright MCP

Preconditions:

- Seeded baseline. Use a second browser tab with no login for the kiosk.

- **Idle.** Open `/kiosk.php` in a new tab. It shows a large random name (for example `great stage`), the window size, and the address.
- **Assign.** In the coordinator tab, open `/kiosk-dashboard.php`. The new kiosk is listed. Choose a page for it, for example `Now Racing`.
- **Switch.** Select the kiosk tab and wait up to 5 s, the poll interval. The kiosk shows the chosen page.
- **Proof.** Screenshot the kiosk before and after, and the dashboard row.

## Gotchas

- The kiosk polls every 5 s. Wait for it before asserting. ADR-15 asks the Django port for under 1 s.
- `/awards-presentation.php` alerts `There are NO kiosks ready for award presentation` until a kiosk shows the awards page.
- A kiosk tab in the background is throttled and may miss a poll. Select it before waiting.
