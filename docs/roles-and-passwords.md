# Roles and passwords

DerbyNet has no user accounts. Each job on race day has a role, and everyone doing that job logs in with the role's shared password.

## The default roles

A new install starts with these roles and passwords:

| Role | Password | What it is for |
|---|---|---|
| RaceCoordinator | `doyourbest` | Everything: set-up, settings, racing, awards, and passwords. |
| RaceCrew | `murphy` (as in Don Murphy, not Murphy's Law) | Check-in, adding and editing racers, photos, and judging. Not set-up, and not racing control. |
| Photo | `flashbulb` | Uploading photos, for a photo stand. The login page lists it only after "Show all roles". |
| Timer | none | Reporting heats, for `derby-timer.jar`. The login page lists it only after "Show all roles". |

A visitor who has not logged in can open the kiosk pages, the slideshow, racers on deck, results by racer, the vote page, standings, the results export, the about page, and these guides.

To log in, tap "Log in" at the top of the page or in the phone's bottom bar, pick the role, and type its password.

## Change a password

While a role keeps its default password, every page warns a logged-in user. The warning names the roles, and a race coordinator gets a "Change passwords" link.

1. Log in as RaceCoordinator, or another role with set-up rights.
2. Open Set-Up, then "Roles and passwords" under "Before race day". The page is at `/settings/roles/`.
3. On the role's card, type the new password under "New password for", and press "Change password".

The card says "Password changed for every event." Roles belong to the server, not to one event, so the new password works for every event on it at once (D79).

An empty password is refused, except for Timer. Timer starts with none, and its card has a "No password" box to keep it that way (D79).

A device already logged in keeps its login after a password change, until its login expires 24 hours after it logged in. To be sure nobody keeps the old password, log out on each device and log in again.

The anonymous visitor's role is listed too, with what it can see. It has no password.

The roles and what each one may do are fixed. The page changes only passwords.

## The timer laptop

The laptop that runs the browser timer logs in as RaceCoordinator (D53). The browser timer shows its Timer settings button only to a role that controls the race.

`derby-timer.jar` logs in as Timer. If you give Timer a password, type the same password into `derby-timer.jar`'s settings.

## The photo stand

A Raspberry Pi photo stand uploads photos as the Photo role. If you change the Photo password, change it in the photo stand's settings too. See [the photo stand guide](photo-stand.md).
