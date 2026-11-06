"""What the set-up page shows (#48): the first-run wizard's steps, each with
its status and one primary action, the setup health checks, and the data
section's counts."""

from dataclasses import dataclass, field

from accounts.permissions import role
from core import diagnostics, setup_details
from core.diagnostics import Status
from core.models import Event, RaceSettings
from core.settings_page import BY_KEY
from core.setup_event import post_setup_role
from roster.services.racers import FAKE_DEFAULTS, FAKE_GROUPS, FAKE_PER_GROUP
from transfer import backups
from ui.settings_page.state import field_view


@dataclass
class Step:
    key: str
    title: str
    # ok, unknown (still to do), or trouble.
    status: str
    detail: str = ""


@dataclass
class Purge:
    what: str
    title: str
    count: int
    noun: str
    noun_plural: str
    body: str


@dataclass
class Wizard:
    event: Event | None
    events: list[Event]
    steps: list[Step]
    checks: list[diagnostics.Check]
    tz: diagnostics.TimeZone
    pending: list[str]
    role: str | None
    post_setup: str
    lanes: object = None
    folders: list = field(default_factory=list)
    nracers: int = 0
    purges: list[Purge] = field(default_factory=list)
    reinitialize: int = 0
    snapshots: list = field(default_factory=list)
    fake: dict = field(default_factory=dict)


def _purges(counts: dict) -> list[Purge]:
    """`setup.php`'s purge dialog, with each count from the setup details.
    Deleting schedules or racers deletes their results too (`setup.js`'s
    `confirm_purge()`)."""
    return [
        Purge(
            "results",
            "Delete race results",
            counts["nresults"],
            "completed heat",
            "completed heats",
            "Every heat result goes. The schedules and the racers stay.",
        ),
        Purge(
            "schedules",
            "Delete schedules",
            counts["nheats"],
            "scheduled heat",
            "scheduled heats",
            "Every round's heats go, and with them every result.",
        ),
        Purge(
            "racers",
            "Delete racers",
            counts["nracers"],
            "racer",
            "racers",
            "Every racer goes, with their results.",
        ),
        Purge(
            "awards",
            "Delete awards",
            counts["nawards"],
            "award",
            "awards",
            "Every award goes. The racers and results stay.",
        ),
    ]


def build(request, event: Event | None) -> Wizard:
    events = list(Event.objects.order_by("-created_at"))
    pending = diagnostics.pending_migrations()
    tz = diagnostics.time_zone()
    checks = diagnostics.health_checks(event)
    post_setup = post_setup_role()
    current = role(request)
    steps = [
        Step(
            "event",
            "Create or open an event",
            Status.OK if event else Status.UNKNOWN,
            f"The open event is {event.name}." if event else "No event yet.",
        ),
        Step(
            "schema",
            "Database migrations",
            Status.TROUBLE if pending else Status.OK,
            f"{len(pending)} not applied." if pending else "Up to date.",
        ),
        Step("time-zone", "Time zone", Status.OK, tz.name),
    ]
    wizard = Wizard(
        event=event,
        events=events,
        steps=steps,
        checks=checks,
        tz=tz,
        pending=pending,
        role=current,
        post_setup=post_setup.name if post_setup else "",
    )
    if event is not None:
        row = RaceSettings.for_event(event)
        details = setup_details.build(event)
        counts = details["purge"]
        folders = [c for c in checks if c.key in ("photo-dir", "car-photo-dir")]
        steps += [
            Step(
                "lanes",
                "Lanes on the track",
                Status.OK if row.lane_count else Status.UNKNOWN,
                f"{row.lane_count} lanes." if row.lane_count else "Not set.",
            ),
            Step(
                "photos",
                "Photo folders",
                Status.OK
                if all(c.status == Status.OK for c in folders)
                else Status.TROUBLE,
                "Racer and car photo folders.",
            ),
            Step(
                "roster",
                "Roster",
                Status.OK if counts["nracers"] else Status.UNKNOWN,
                f"{counts['nracers']} racers." if counts["nracers"] else "No racers.",
            ),
        ]
        wizard.lanes = field_view(event, BY_KEY["n-lanes"])
        wizard.folders = folders
        wizard.nracers = counts["nracers"]
        wizard.purges = _purges(counts)
        wizard.reinitialize = counts["nracers"]
        wizard.snapshots = backups.snapshots(event)[:5]
    steps.append(
        Step(
            "login",
            "Log in for race day",
            Status.OK if current else Status.UNKNOWN,
            f"Logged in as {current}." if current else "Not logged in.",
        )
    )
    wizard.fake = FAKE
    return wizard


# The fake roster sheet's ranges and defaults, from the service.
FAKE = {"groups": FAKE_GROUPS, "per_group": FAKE_PER_GROUP, "defaults": FAKE_DEFAULTS}
