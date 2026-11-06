"""The navigation registry (#39, #40, D67).

One `NavEntry` per manager page drives the page's URL, its permission guard,
the home page, the desktop sidebar, the phone bottom nav and its "More"
drawer. A link shows exactly when its page lets the visitor in, because both
read the entry's `permission` (C36).

A later M4 ticket builds its page by setting the entry's `view`. Until then
the page is a placeholder that names the ticket, so the guard, the menus and
the all-pages smoke test already cover it. A page section that has its own
URL, such as a polled partial, takes the same guard with `requires_page()`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps

from django.urls import URLPattern, path
from django.utils.module_loading import import_string

from accounts.permissions import Access, Perm, Requirement, allows, role
from accounts.views import page_requires


class Section(StrEnum):
    """The groups of `website/index.php`, in its order."""

    BEFORE = "Before"
    DURING = "During"
    AFTER = "After"
    OTHER = "Other"


# The phone rank of a role the D20 table does not name.
OTHER_ROLES = "*"
ANONYMOUS_ROLE = ""
# The roles the D20 table names. Any other role ranks by the `*` column.
D20_ROLES = frozenset({"RaceCoordinator", "RaceCrew", "Photo", ANONYMOUS_ROLE})
# Roles for a device, which get no bottom nav (#39).
NO_PHONE_NAV = frozenset({"Timer"})
PHONE_SLOTS = 3


@dataclass(frozen=True, eq=False)
class NavEntry:
    # The URL name.
    key: str
    # The menu label and the page title.
    label: str
    # The URL path, relative to the server root.
    path: str
    permission: Requirement
    section: Section
    # The ticket that builds the page.
    ticket: int
    # The PHP page it replaces. The placeholder names it, and once `view` is
    # set the page answers it with a 301 (`ui.redirects`).
    php: str = ""
    # The label in the phone bottom nav.
    short_label: str = ""
    icon: str = "page"
    # Role name ("" for anonymous, "*" for any role D20 does not name) to the
    # slot in that role's phone bottom nav, 1 first.
    phone_rank: Mapping[str, int] = field(default_factory=dict)
    # False for a page reached from another page, not from the menus.
    menu: bool = True
    # The view's dotted path. Empty serves the placeholder.
    view: str = ""
    # A callable's dotted path that sees the page request before its guard,
    # and may answer it: the check-in page's QR token grants the permission
    # the guard then checks (#42).
    pre_guard: str = ""

    @property
    def phone_label(self) -> str:
        return self.short_label or self.label


ANON = Access.ANONYMOUS
BEFORE, DURING, AFTER, OTHER = Section

# Menu entries are in `index.php` order. Permissions are the page gates of
# #15's "Page permissions": the race dashboard needs CONTROL_RACE (D16),
# standings, slideshow, on deck, results by racer and about are anonymous (D5,
# D70, S10, S30), and timer-test needs CONTROL_RACE (S21). Printables are removed
# (D6). Vote is anonymous: its login is the ballot password (ADR-07, #54).
# Phone ranks are the D20 table of #39.
ENTRIES: tuple[NavEntry, ...] = (
    NavEntry(
        "home",
        "Home",
        "",
        ANON,
        OTHER,
        40,
        php="index.php",
        icon="home",
        menu=False,
        view="ui.views.home",
    ),
    NavEntry(
        "login",
        "Log In",
        "login/",
        ANON,
        OTHER,
        40,
        php="login.php",
        menu=False,
        view="accounts.views.login",
    ),
    # Before
    NavEntry(
        "setup",
        "Set-Up",
        "setup/",
        Perm.SET_UP,
        BEFORE,
        48,
        php="setup.php",
        icon="setup",
        view="ui.setup.views.page",
    ),
    NavEntry(
        "checkin",
        "Race Check-In",
        "checkin/",
        Perm.CHECK_IN_RACERS,
        BEFORE,
        42,
        php="checkin.php",
        short_label="Check-In",
        icon="checkin",
        phone_rank={"RaceCoordinator": 2, "RaceCrew": 1, "Photo": 2, OTHER_ROLES: 2},
        view="ui.checkin.views.page",
        pre_guard="ui.checkin.views.accept_token",
    ),
    NavEntry(
        "racer-photos",
        "Racer Photos",
        "photos/racers/",
        Perm.ASSIGN_RACER_IMAGE,
        BEFORE,
        47,
        php="photo-thumbs.php?repo=head",
        short_label="Photos",
        icon="photos",
        phone_rank={"RaceCrew": 2, "Photo": 1, OTHER_ROLES: 4},
        view="ui.photos.views.page",
    ),
    NavEntry(
        "car-photos",
        "Car Photos",
        "photos/cars/",
        Perm.ASSIGN_RACER_IMAGE,
        BEFORE,
        47,
        php="photo-thumbs.php?repo=car",
        icon="car",
        view="ui.photos.views.page",
    ),
    # During
    NavEntry(
        "dashboard",
        "Race Dashboard",
        "dashboard/",
        Perm.CONTROL_RACE,
        DURING,
        41,
        php="coordinator.php",
        short_label="Dashboard",
        icon="dashboard",
        phone_rank={"RaceCoordinator": 1, OTHER_ROLES: 1},
        view="ui.dashboard.views.dashboard",
    ),
    NavEntry(
        "kiosk-dashboard",
        "Kiosk Dashboard",
        "kiosks/",
        Perm.PRESENT_AWARDS,
        DURING,
        46,
        php="kiosk-dashboard.php",
        icon="kiosk",
        view="ui.kiosks.views.page",
    ),
    NavEntry(
        "judging",
        "Judging",
        "judging/",
        Perm.JUDGING,
        DURING,
        45,
        php="judging.php",
        icon="judging",
        view="ui.judging.views.page",
    ),
    NavEntry(
        "slideshow",
        "Slideshow",
        "slideshow/",
        ANON,
        DURING,
        52,
        php="slideshow.php",
        icon="slideshow",
        phone_rank={ANONYMOUS_ROLE: 3},
        view="ui.kiosk_pages.views.slideshow",
    ),
    NavEntry(
        "ondeck",
        "Racers On Deck",
        "ondeck/",
        ANON,
        DURING,
        52,
        php="ondeck.php",
        short_label="On Deck",
        icon="ondeck",
        phone_rank={"RaceCrew": 3, ANONYMOUS_ROLE: 2, OTHER_ROLES: 5},
        view="ui.kiosk_pages.views.ondeck",
    ),
    NavEntry(
        "racer-results",
        "Results By Racer",
        "results/",
        ANON,
        DURING,
        49,
        php="racer-results.php",
        short_label="Results",
        icon="results",
        phone_rank={ANONYMOUS_ROLE: 1, OTHER_ROLES: 6},
        view="ui.results.views.page",
    ),
    # `index.php:137` shows Vote! to VIEW_RACE_RESULTS, which the anonymous
    # role has; the voter's login is the ballot password (#54, ADR-07).
    NavEntry(
        "vote",
        "Vote",
        "vote/",
        ANON,
        DURING,
        54,
        php="vote.php",
        icon="vote",
        view="ui.vote.views.page",
    ),
    # After
    NavEntry(
        "awards-presentation",
        "Present Awards",
        "awards/present/",
        Perm.PRESENT_AWARDS,
        AFTER,
        45,
        php="awards-presentation.php",
        icon="awards",
        view="ui.presenter.views.page",
    ),
    NavEntry(
        "standings",
        "Standings",
        "standings/",
        ANON,
        AFTER,
        49,
        php="standings.php",
        icon="standings",
        phone_rank={"RaceCoordinator": 3, OTHER_ROLES: 3},
        view="ui.standings.views.page",
    ),
    NavEntry(
        "export",
        "Export Results",
        "export/",
        Perm.VIEW_RACE_RESULTS,
        AFTER,
        49,
        php="export.php",
        icon="export",
        view="ui.exports.views.page",
    ),
    NavEntry(
        "history",
        "Retrospective",
        "history/",
        Perm.CHECK_IN_RACERS,
        AFTER,
        49,
        php="history.php",
        icon="history",
        view="ui.history.views.page",
    ),
    # Other
    NavEntry(
        "about",
        "About",
        "about/",
        ANON,
        OTHER,
        48,
        php="about.php",
        icon="about",
        view="ui.about.views.page",
    ),
    # Reached from other pages (#39 Context).
    NavEntry(
        "settings",
        "Settings",
        "settings/",
        Perm.SET_UP,
        BEFORE,
        48,
        php="settings.php",
        menu=False,
        view="ui.settings_page.views.page",
    ),
    NavEntry(
        "roles",
        "Roles and Passwords",
        "settings/roles/",
        Perm.SET_UP,
        BEFORE,
        48,
        menu=False,
        view="ui.roles.views.page",
    ),
    NavEntry(
        "racing-groups",
        "Racing Groups",
        "groups/",
        Perm.SET_UP,
        BEFORE,
        43,
        php="racing-groups.php",
        menu=False,
        view="ui.groups.views.page",
    ),
    NavEntry(
        "playlist",
        "Rounds Playlist",
        "playlist/",
        Perm.SET_UP,
        BEFORE,
        44,
        php="playlist.php",
        menu=False,
        view="ui.playlist.views.page",
    ),
    NavEntry(
        "scenes",
        "Scenes",
        "kiosks/scenes/",
        Perm.SET_UP,
        DURING,
        46,
        php="scenes.php",
        menu=False,
        view="ui.scenes.views.page",
    ),
    NavEntry(
        "import-roster",
        "Import Roster",
        "import/roster/",
        Perm.SET_UP,
        BEFORE,
        49,
        php="import-roster.php",
        menu=False,
        view="ui.imports.views.roster_page",
    ),
    NavEntry(
        "import-awards",
        "Import Awards",
        "import/awards/",
        Perm.SET_UP,
        BEFORE,
        49,
        php="import-awards.php",
        menu=False,
        view="ui.imports.views.awards_page",
    ),
    NavEntry(
        "import-results",
        "Import Results",
        "import/results/",
        Perm.SET_UP,
        AFTER,
        49,
        php="import-results.php",
        menu=False,
        view="ui.imports.views.results_page",
    ),
    NavEntry(
        "awards-editor",
        "Awards Editor",
        "awards/edit/",
        Perm.EDIT_AWARDS,
        BEFORE,
        45,
        php="awards-editor.php",
        menu=False,
        view="ui.awards.views.page",
    ),
    NavEntry(
        "fakeroster",
        "Fake Roster",
        "setup/fake-roster/",
        # C56: SET_UP, as racer.fake needs (`action.racer.fake.inc:26`).
        # PHP's page asks for CHECK_IN_RACERS (`fakeroster.php:10`), so a role
        # without SET_UP saw a button that did nothing (#48).
        Perm.SET_UP,
        BEFORE,
        48,
        php="fakeroster.php",
        menu=False,
        view="ui.setup.views.fake_roster",
    ),
    NavEntry(
        "fake-timer",
        "Fake Timer",
        "timer/fake/",
        Perm.CHECK_IN_RACERS,
        DURING,
        50,
        php="fake-timer.php",
        menu=False,
        view="ui.timer.views.fake_page",
    ),
    # The timer status page: the summary half of timer-test.php, which the
    # race dashboard's "Timer status" link opens on a phone (#50).
    NavEntry(
        "timer",
        "Timer",
        "timer/",
        Perm.CONTROL_RACE,
        DURING,
        50,
        menu=False,
        view="ui.timer.views.page",
    ),
    NavEntry(
        "timer-test",
        "Timer Test",
        "timer/test/",
        Perm.CONTROL_RACE,
        DURING,
        50,
        php="timer-test.php",
        menu=False,
        view="ui.timer.views.test_page",
    ),
    # The replay station and its remote camera (#53) need CONTROL_RACE, the
    # replay login (D5, D9, D16). The replay entry is in the Photo role's
    # bottom nav of the D20 table, for a role a pack gives CONTROL_RACE
    # (D68). The login page's Replay Kiosk and Be a Camera buttons link to
    # replay.php and camera.php, which answer with a 301 here.
    NavEntry(
        "replay",
        "Replay",
        "replay/",
        Perm.CONTROL_RACE,
        DURING,
        53,
        php="replay.php",
        icon="replay",
        phone_rank={"Photo": 3},
        menu=False,
        view="ui.replay.views.page",
    ),
    NavEntry(
        "camera",
        "Replay Camera",
        "replay/camera/",
        Perm.CONTROL_RACE,
        DURING,
        53,
        php="camera.php",
        icon="replay",
        menu=False,
        view="ui.replay.views.camera",
    ),
)

BY_KEY: dict[str, NavEntry] = {e.key: e for e in ENTRIES}


def entry(key: str) -> NavEntry:
    return BY_KEY[key]


def can_open(request, nav_entry: NavEntry) -> bool:
    return allows(request, nav_entry.permission)


def menu_entries(request) -> list[NavEntry]:
    """The menu entries the request may open, in menu order."""
    return [e for e in ENTRIES if e.menu and can_open(request, e)]


def by_section(entries) -> list[tuple[Section, list[NavEntry]]]:
    """`entries` grouped by section, in section order, leaving out empty ones."""
    return [
        (s, group) for s in Section if (group := [e for e in entries if e.section == s])
    ]


def _phone_role(request) -> str:
    return role(request) or ANONYMOUS_ROLE


def bottom_nav(request) -> list[NavEntry] | None:
    """The role's phone bottom nav before its last button, or None for a role
    with none. A role D20 does not name ranks by the `*` column."""
    name = _phone_role(request)
    if name in NO_PHONE_NAV:
        return None
    column = name if name in D20_ROLES else OTHER_ROLES
    ranked = [
        (rank, e)
        for e in ENTRIES
        if (rank := e.phone_rank.get(column)) and can_open(request, e)
    ]
    return [e for _, e in sorted(ranked, key=lambda pair: pair[0])][:PHONE_SLOTS]


def requires_page(key: str) -> Callable:
    """The page guard of the registry entry `key`, for its view and for every
    URL of a section of the page."""
    nav_entry = BY_KEY[key]

    def decorate(view):
        guarded = page_requires(nav_entry.permission)(view)

        @wraps(view)
        def with_entry(request, *args, **kwargs):
            request.nav_entry = nav_entry
            return guarded(request, *args, **kwargs)

        return with_entry

    return decorate


def _view(nav_entry: NavEntry):
    if nav_entry.view:
        return import_string(nav_entry.view)
    from ui.views import placeholder

    return placeholder


def _page(nav_entry: NavEntry):
    guarded = requires_page(nav_entry.key)(_view(nav_entry))
    if not nav_entry.pre_guard:
        return guarded
    before = import_string(nav_entry.pre_guard)

    @wraps(guarded)
    def page(request, *args, **kwargs):
        return before(request) or guarded(request, *args, **kwargs)

    return page


def urlpatterns() -> list[URLPattern]:
    return [path(e.path, _page(e), name=e.key) for e in ENTRIES]
