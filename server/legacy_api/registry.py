"""The legacy `/action.php` endpoints and what a handler sees (#17).

A handler is `def handler(ctx: LegacyContext) -> None`, registered under its
exact PHP name with `@action(...)` or `@query(...)`. It reads `ctx.args`,
calls services, and writes the response through `ctx` the way the PHP
handlers write `$json_out` or echo XML: the last outcome wins.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

from django.conf import settings
from django.db.models import Model

from accounts.defaults import DEFAULT_ROLES
from accounts.permissions import Perm, Requirement
from core.models import Event
from legacy_api.formats import Format, format_of
from legacy_api.missing import MISSING
from legacy_api.parity_paths import ANY, ParityPath
from legacy_api.xml import XmlWriter

logger = logging.getLogger(__name__)

Kind = Literal["action", "query"]
FailureCode = Literal["notauthorized", "permission"]


# The description PHP gives with each refusal code, in JSON. The XML refusal
# is always not_authorized_body().
REFUSALS: dict[FailureCode, str] = {
    "notauthorized": "Not authorized -- please see race coordinator.",
    "permission": "Not authorized",
}

# #17 excludes these from the action log, as polling noise or as secrets.
# PHP's list names `replay-message`, which no action has (#64 C22).
UNLOGGED_ACTIONS = frozenset(
    {"role.login", "vote.cast", "replay.message", "message.send", "message.retrieve"}
)


# A past event's refusal (#212): every action answers `event-read-only` on a
# read-only event, from its write service's `lock_race_state`, except these.
# `register()` adds READ_ONLY_DIVERGENCE to every other action.
READ_ONLY_EXEMPT: dict[str, str] = {
    "timer-message": "D81: the timer's own bookkeeping; FINISHED is refused",
    "message.retrieve": "D81: a kiosk or replay station reading its queue",
    "message.send": "D81: the replay page and its camera signal WebRTC",
    "replay.message": "D81: the replay station reporting its state",
    # Site-level: none of these writes an event's rows.
    "role.login": "site-level: roles are site-wide (ADR-07), and the session",
    "login": "site-level: roles are site-wide (ADR-07), and the session",
    "session.write": "site-level: the visitor's session",
    "setup.nodata": "site-level: makes or opens an event, the lifecycle (D81)",
    "file.directory.new.nodata": "site-level: a folder under the data folder",
    # Registered only with DERBYNET_ENABLE_TEST_ENDPOINTS. It copies a slide
    # into the event's slides folder, for the shell suites.
    "test.setup": "test endpoint: seeds a slide for the shell suites",
}
READ_ONLY_DIVERGENCE = (
    "D14, D81: on a read-only (past) event it answers event-read-only and"
    " changes nothing; PHP has no read-only event."
)


def needs_event(name: str) -> bool:
    """Only `.nodata` names run with no active event. PHP redirects the rest,
    unknown names included, to the setup page."""
    return not name.endswith(".nodata")


def is_logged(name: str, args: Mapping) -> bool:
    """Whether an action goes to `ActionLog`. PHP has no database for a
    `.nodata` action, so it logs none of them."""
    if name == "timer-message" and args.get("message") == "HEARTBEAT":
        return False
    return name not in UNLOGGED_ACTIONS and needs_event(name)


@dataclass(frozen=True)
class Endpoint:
    kind: Kind
    name: str
    handler: Callable[[LegacyContext], None]
    permission: Requirement
    failure_code: FailureCode = "notauthorized"
    # Only role.login, ballot.get, session.write and setup.nodata write it.
    session_write: bool = False
    # A query that writes on GET, as #17 lists them. No other query may.
    get_writes: bool = False
    # Each intentional difference from PHP's reply, as `<id>: <what>`, where
    # the id is a #64 row, an owner decision or a quirk of #20. The parity
    # gate (#37) allows exactly these. The guard in
    # legacy_api/tests/test_registry.py accepts an id from QUIRK_DECISIONS
    # (racing/engine/tests/golden.py), a quirk in deviations.json, or a `D<n>`
    # row of plan/owner-decisions.md, so add the decision before citing it.
    divergences: tuple[str, ...] = ()

    def __post_init__(self):
        if self.failure_code == "permission" and self.fmt is Format.XML:
            raise ValueError(f"{self.name}: XML refusals are always notauthorized")
        if self.get_writes and self.kind != "query":
            raise ValueError(f"{self.name}: get_writes applies to queries only")

    @property
    def fmt(self) -> Format:
        return format_of(self.name)

    @property
    def needs_event(self) -> bool:
        return needs_event(self.name)


class Registry:
    def __init__(self):
        self._endpoints: dict[tuple[Kind, str], Endpoint] = {}

    def register(self, endpoint: Endpoint) -> None:
        if endpoint.kind == "action" and endpoint.name not in READ_ONLY_EXEMPT:
            endpoint = replace(
                endpoint,
                divergences=(*endpoint.divergences, READ_ONLY_DIVERGENCE),
            )
        key = (endpoint.kind, endpoint.name)
        if key in self._endpoints:
            raise ValueError(f"{endpoint.kind} {endpoint.name} is registered twice")
        self._endpoints[key] = endpoint

    def unregister(self, endpoint: Endpoint) -> None:
        del self._endpoints[(endpoint.kind, endpoint.name)]

    def get(self, kind: Kind, name: str) -> Endpoint | None:
        return self._endpoints.get((kind, name))

    def __iter__(self) -> Iterator[Endpoint]:
        return iter(list(self._endpoints.values()))


REGISTRY = Registry()


@dataclass(frozen=True)
class Deferred:
    """A PHP endpoint Django does not serve yet, and the backlog ticket that
    ports it. The coverage check of #37 counts it as accounted for."""

    kind: Kind
    name: str
    ticket: str
    reason: str


# Every PHP endpoint deferred to a backlog ticket. A name here is never
# registered, so the dispatcher answers it as it answers any unknown name.
# None is deferred today: #54 serves the ballot endpoints.
DEFERRED: tuple[Deferred, ...] = ()


@dataclass(frozen=True)
class Removed:
    """A PHP endpoint Django drops on purpose. `reason` cites the decision
    (`D48: ...`). The coverage check of #37 counts it as accounted for."""

    kind: Kind
    name: str
    reason: str


# Every PHP endpoint Django drops for good. A name here is never registered
# or deferred, and the dispatcher answers it as any unknown name. No PHP
# handler is dropped today.
REMOVED: tuple[Removed, ...] = ()


# Every reply that carries the coordinator payload's `current-heat` (R3).
_CURRENT_HEAT = (
    "action heat.select",
    "action heat.rerun",
    "action heat.reinstate",
    "action result.write",
    "action result.delete",
    "action schedule.generate",
    "action schedule.unschedule",
    "action schedule.reschedule",
    "action roster.new",
    "action roster.delete",
    "query poll.coordinator",
    "query poll",
    "query poll.results",
)
_KIOSK_LISTS = ("query poll.kiosk.all", "action kiosk.assign", "action scene.apply")

# The default roles (accounts/defaults.py), as the shell suite logs in, and
# those of them without a permission; "" is no login.
_ROLES = {name: permissions for name, _, permissions, _ in DEFAULT_ROLES}


def _without(permission: int) -> tuple[str, ...]:
    return tuple(sorted(name for name, held in _ROLES.items() if not held & permission))


# The endpoints Django refuses where PHP answers anyone, with the divergence,
# the roles it covers, and the keys of PHP's answer.
_REFUSES = (
    # D5: a login; RaceCoordinator, RaceCrew, Timer and Photo all get the list.
    ("query class.list", "D5", ("",), ("classes",)),
    # S27: PRESENT_AWARDS, as kiosk-dashboard.php:13 requires.
    (
        "query poll.kiosk.all",
        "S27",
        _without(Perm.PRESENT_AWARDS),
        ("current-scene", "kiosks", "kiosk-pages"),
    ),
)
_REFUSED = {
    "summary": "failure",
    "code": "notauthorized",
    "description": REFUSALS["notauthorized"],
}
# The page PHP seeds for Aux2 in its Wrap-Up scene (D10, #52), and the index
# of Wrap-Up among the scenes both servers seed (core.services.LEGACY_SCENES,
# website/sql/sqlite/scene-tables.inc); a test keeps it true.
_FEEDBACK = "local/kiosks/feedback.kiosk"
_WRAP_UP = 5

# Each reply path a recorded divergence changes, as the parity gate saw it,
# with the values the divergence names. Add one only for a difference that an
# existing divergence of that endpoint already explains; any other difference
# fails the gate. The divergence must be listed on the endpoint
# (`test_every_parity_path_cites_a_divergence_of_its_endpoint` in
# tests/test_parity_gate.py), and its id must be a key of QUIRK_DECISIONS
# (racing/engine/tests/golden.py), a quirk of tests/golden/deviations.json or
# a D row of plan/owner-decisions.md
# (`test_every_divergence_names_a_recorded_decision` in
# legacy_api/tests/test_registry.py). A #64 row the ticket leaves to the owner
# is none of these until the owner decides it.
PARITY_PATHS: tuple[ParityPath, ...] = (
    *(
        ParityPath(endpoint, f"$.current-heat.{key}", "R3", php=-1, django=None)
        for endpoint in _CURRENT_HEAT
        for key in ("classid", "heat")
    ),
    # PHP repeats each of the eight columns under its position, "0" to "7".
    *(
        ParityPath("query poll.results", f'$.results[*]["{n}"]', "R9", django=MISSING)
        for n in range(8)
    ),
    *(
        ParityPath(endpoint, f"$.awards[*].{key}", "A1", php=None, django=0)
        for endpoint in (
            "action award.edit",
            "action award.delete",
            "action award.order",
            "action award.adhoc",
            "action award.winner",
            "query award.list",
        )
        for key in ("classid", "rankid")
    ),
    *(
        # The places of a reinstated heat, which PHP's reinstate leaves empty.
        ParityPath(endpoint, "$.racers[*].finishplace", "Q4", php="", django=int)
        for endpoint in ("action heat.reinstate", "query poll.coordinator")
    ),
    *(
        # The scene applied last, by id.
        ParityPath(endpoint, "$.current-scene", "C19", php="", django=int)
        for endpoint in _KIOSK_LISTS
    ),
    *(
        # The kiosk's own parameters, as the JSON text it stores.
        ParityPath(endpoint, "$.kiosks[*].parameters", "C20", php=None, django=str)
        for endpoint in _KIOSK_LISTS
    ),
    *(
        # The rotation scheduler's debug output.
        ParityPath("action schedule.generate", f"$.{key}", "R6", django=MISSING)
        for key in ("nheats", "ncars", "heats")
    ),
    ParityPath("action schedule.reschedule", "$.adjuster", "R6", django=MISSING),
    ParityPath("action result.delete", "$.sql", "C23", django=MISSING),
    ParityPath("query settings.list", "$.settings.db-connection", "S2", django=""),
    *(
        # What each reset ran, and the database and engine it ran on.
        ParityPath(endpoint, path, "D67")
        for endpoint in (
            "action database.execute",
            "action database.purge",
            "action setup.nodata",
        )
        for path in ("$.executed*", "$.details.*")
    ),
    *(
        # testing/test-photo-manipulations.sh measures the renders of
        # pattern-o6.jpg, stored 320x480 with EXIF orientation 6: PHP keeps the
        # stored pixels and drops the tag, Django turns them upright.
        ParityPath("page photo.php", "text", "P1", php=php, django=django)
        for php, django in (
            ("133x200 none GWRB", "200x133 none RGBW"),
            ("400x600 none GWRB", "600x400 none RGBW"),
        )
    ),
    # A refusal where PHP answers: Django's notauthorized outcome, and none of
    # the keys PHP's answer has.
    *(
        ParityPath(
            endpoint, "$.outcome", divergence, php=MISSING, django=_REFUSED, roles=roles
        )
        for endpoint, divergence, roles, _ in _REFUSES
    ),
    *(
        ParityPath(endpoint, f"$.{key}", divergence, django=MISSING, roles=roles)
        for endpoint, divergence, roles, keys in _REFUSES
        for key in keys
    ),
    # replay.message from a role without CONTROL_RACE (D16): PHP answers
    # success, and Django refuses.
    *(
        ParityPath(
            "action replay.message",
            f"$.outcome.{key}",
            "D16",
            php=php,
            django=dj,
            roles=_without(Perm.CONTROL_RACE),
        )
        for key, php, dj in (
            ("summary", "success", "failure"),
            ("code", "success", "notauthorized"),
            ("description", "", REFUSALS["notauthorized"]),
        )
    ),
    ParityPath(
        "action replay.message",
        "$.replay",
        "D16",
        php=list,
        django=MISSING,
        roles=_without(Perm.CONTROL_RACE),
    ),
    # #52's Wrap-Up seed (D10): PHP's Aux2 on local/kiosks/feedback.kiosk,
    # which Django leaves out, so the Wrap-Up scene's kiosks after it move up
    # one, and a kiosk named Aux2 keeps the page it had. Wrap-Up is the
    # sixth seeded scene (`_WRAP_UP`), and every seeded scene has a kiosk, so
    # it is at that index in both lists until a scene is added or removed.
    ParityPath(
        "query scene.list",
        f"$.scenes[{_WRAP_UP}].kiosks[*].kiosk_name",
        "D10",
        php="Aux2",
        django="Main",
    ),
    ParityPath(
        "query scene.list",
        f"$.scenes[{_WRAP_UP}].kiosks[*].page",
        "D10",
        php=_FEEDBACK,
        django="kiosks/standings.kiosk",
    ),
    *(
        ParityPath(endpoint, path, "D10", php=kiosk, django=MISSING)
        for endpoint, path in (
            ("query scene.list", f"$.scenes[{_WRAP_UP}].kiosks[*]"),
            ("action scene.setkiosk", f"$.all-scenes[{_WRAP_UP}].kiosks[*]"),
        )
        for kiosk in (
            {
                "kiosk_name": "Main",
                "page": "kiosks/standings.kiosk",
                "parameters": "{}",
            },
            {"kiosk_name": "Aux2", "page": _FEEDBACK, "parameters": "{}"},
        )
    ),
    ParityPath(
        "action scene.apply", "$.kiosks[*].page", "D10", php=_FEEDBACK, django=str
    ),
    ParityPath(
        "query poll.kiosk", "$.kiosk-setting.page", "D10", php=_FEEDBACK, django=str
    ),
)


def divergence_text(endpoint: str, divergence: str) -> str | None:
    """The endpoint's divergence entry whose ids include `divergence`. A
    `page <path>` endpoint's entries are its `ROUTE_DIVERGENCES`."""
    kind, _, name = endpoint.partition(" ")
    if kind == "page":
        texts = ROUTE_DIVERGENCES.get(name, ())
    else:
        found = REGISTRY.get(kind, name)
        texts = found.divergences if found else ()
    for text in texts:
        ids = text.split(": ", 1)[0].split(", ")
        if divergence in ids:
            return text
    return None


def allowed_by(
    endpoint: str, path: str, php=ANY, django=ANY, role: str | None = None
) -> str | None:
    """The divergence that lets Django's reply differ from PHP's at `path`,
    where PHP gave `php` and Django `django` to a session logged in as
    `role` (`""` for none, None when unknown), or None. The parity gate
    fails on a difference this doesn't allow."""
    for allowed in PARITY_PATHS:
        if allowed.covers(endpoint, path, php, django, role):
            return divergence_text(endpoint, allowed.divergence)
    return None


# The legacy pages outside `/action.php` that differ from PHP on purpose, by
# path, in the form of `Endpoint.divergences`.
ROUTE_DIVERGENCES: dict[str, tuple[str, ...]] = {
    "photo.php": (
        "P1: a render applies the photo's EXIF orientation, so a photo stored on"
        " its side renders upright; PHP renders the stored pixels and drops the"
        " tag.",
    ),
    "timer.php": (
        "D5: needs TIMER_MESSAGE, the Timer role's one permission; PHP's page"
        " needs SET_UP.",
        "D53: the Timer settings button shows only to a session with"
        " CONTROL_RACE, which timer.settings needs.",
        "D72: a browser without Web Serial is sent to /timer/no-serial/"
        " before any timer script runs; PHP shows its own dialog over the"
        " page.",
    ),
    "timer-test.php": (
        "S21: a 301 to /timer/test/, which needs CONTROL_RACE; PHP's page is"
        " anonymous.",
        "D72: split into the status page /timer/ and the test page /timer/test/.",
        "D72, C52: a lane toggle names the lane's end state and stages a new"
        " test heat at every change (timer.set_test_lane); after a second"
        " tt-mask change PHP leaves the timer on the heat it already staged,"
        " so the timer never hears of the change"
        " (racing/tests/test_timer_test_lanes.py).",
        "D72: the log tail stops while the page is hidden.",
    ),
    "kiosk.php": (
        "D10: the 15 pages are rebuilt (ADR-10) behind a registry keyed by the"
        " legacy page string; any other page string, a local/kiosks/ page or"
        " a path such as ../settings.php, renders identify.kiosk, where PHP"
        " requires the file named.",
        "S28: grants no permission; PHP ORs PRESENT_AWARDS into every"
        " visitor's session, and the award kiosk no longer clears the current"
        " award on load.",
        "S12: please-check-in reads classids as whole numbers and drops any"
        " other list; PHP implodes the parameter into its SQL.",
        "C34: the award kiosk plays confetti.webm only; PHP also names"
        " confetti.mov, which is not in the repository.",
        "D33, D35, D37: the standings kiosk lists racers not eligible for its"
        " speed awards marked with no place, and a time a closed lane dropped"
        " struck through, from #49's standings list; PHP leaves them out with"
        " a footnote.",
    ),
    "ondeck.php": (
        "D10: rebuilt (ADR-10); the running heat glides near the top where"
        " PHP animates Just Finished and Now Staging across the rows.",
        "D73: the lane count is the race's lane count; PHP's page counts the"
        " lanes of the results (get_lane_count_from_results).",
    ),
    "slideshow.php": (
        "D10: rebuilt (ADR-10); each slide fades in over the one before.",
    ),
    "replay.php": (
        "D20: a 301 to /replay/, the query string kept.",
        "D5, D9, D16: needs CONTROL_RACE, the replay login; PHP's page is"
        " anonymous, so the owner ran it in a browser kept logged out.",
        "D9: the setup picks the camera, the resolution and the frame rate,"
        " opens the camera with them as exact constraints, and this browser"
        " remembers them; PHP asks for the window's size as ideal values and"
        " remembers nothing.",
        "D18: a clip runs from replay-skipback before the finish to the"
        " post-roll after it, its start cut to the longest-clip setting, and"
        " the setup shows these settings with a link to the settings page;"
        " PHP stops recording at the finish and plays the last"
        " replay-skipback ms. The operator's delay counts from the end of the"
        " post-roll, where recording now stops; PHP's counts from the"
        " finish, where its recording stopped.",
        "D76: the replay_ice_servers setting lists the STUN or TURN servers,"
        " PHP's hardcoded Google STUN server by default; empty means host"
        " candidates only, for a LAN with no internet.",
        "C53: a right-click replay names its clip after the current START;"
        " PHP's passes no name and uploads undefined.mkv.",
        "C54: the remote camera's signaling messages are handled one at a"
        " time; PHP handles them at once, and two offers in one poll fail to"
        " set the second answer.",
        "C32: the websocket path flushes the messages sent before the socket"
        " opened with ws.send; PHP calls the undefined wc.send and loses the"
        " first solicitation. The page renders g_websocket_url empty, so v1"
        " polls (#56).",
    ),
    "camera.php": (
        "D20: a 301 to /replay/camera/, the query string kept.",
        "D5, D9, D16: needs CONTROL_RACE, the replay login; PHP's page is anonymous.",
        "D9: this browser remembers the chosen camera.",
        "D76: the replay_ice_servers setting, as replay.php's.",
        "C54: a viewer's solicitation gets one offer, and a viewer that nags"
        " again while not connected gets a new one; PHP offers twice to a new"
        " viewer, and the two offers race at the viewer, and logs a known"
        " viewer's nag as unrecognized, so a viewer that lost its connection"
        " never connects again.",
        "C32: as replay.php's.",
    ),
    "vote.php": (
        "D20: a 301 to /vote/, the query string kept, so a printed link still"
        " opens the ballot.",
        "S2: polls /ballot/status, which answers only whether balloting is"
        " open; vote.js polls settings.list, which answers ballot_password to"
        " anyone who asks for it.",
        "S14, D35: an award's racers are those vote.cast takes, partition"
        " lists included, and a choice the award can no longer take leaves"
        " the voter's list, so the next change replaces it; vote.js filters"
        " by the page's eligible class and rank ids and shows every vote"
        " ballot.get returns.",
        "D77: a change of /ballot/status swaps the page's state in place;"
        " vote.js reloads the page.",
        "D77: a refused vote.cast shows a toast naming the reason; vote.js"
        " ignores the reply.",
        "C33: the sort control keeps ?order= on this page; vote.php's links"
        " open ballot.php, which does not exist.",
        "D47: car numbers show three digits.",
    ),
    "photo.php/info": (
        "S8: removed, and answers 404; PHP serves the file any setting names, or a"
        " path from the URL.",
    ),
    # #48's pages.
    "settings.php": (
        "D20: a 301 to /settings/, the query string kept.",
        "S25: a folder is a text field with a status badge and a Check button,"
        " and must be under the data folder; PHP browses the server's files"
        " and takes any path.",
        "D8, D54: a Weigh-in section sets each partition's weigh-in, and says"
        " before the save that a new mode clears the partition's weigh-ins;"
        " PHP has no weigh-in.",
        "D9, D18: a Replay section sets the speed, the showings, the time"
        " before the finish, the post-roll and the longest clip; PHP sets the"
        " first three in coordinator.php's dialog and has no other two.",
        "D56: the page shows the server's time zone and how to change it.",
        "Q13: loading the page writes nothing; settings.php writes scoring=1"
        " for a legacy drop-slowest key, which the importer converts once.",
        "D80: a number outside its field's range (settings.php's input min and"
        " max, and coordinator.php's replay choices) is refused with an inline"
        " error; PHP stores any number posted.",
    ),
    "setup.php": (
        "D20: a 301 to /setup/, the query string kept.",
        "D2, D57: an event is created or opened among the server's events;"
        " PHP's database dialogs take an SQLite path, an ODBC DSN or a PDO"
        " string, and its schema buttons are a migrations step.",
        "S25: no server file browser.",
        "D14, D81: a switch from the wizard makes the replaced event"
        " read-only, and is refused while the active event's racing flag is"
        " on; PHP switches freely.",
        "D78: each purge, Re-Initialize and snapshot import saves a complete"
        " snapshot first, and the toast links it.",
        "D65: the snapshot import confirms that it replaces all data and names"
        " what a snapshot does not restore.",
        "D56: the wizard shows the server's time zone and how to change it.",
        "D80: the wizard's steps and the health checks replace setup.php's"
        " status list: the database takes writes, the configuration folders,"
        " each photo folder, and Django's upload cap against the photo stand's"
        " 30 MB.",
    ),
    "about.php": (
        "D20: a 301 to /about/, the query string kept.",
        "S30: no phpinfo() or other configuration dump.",
        "C35: the timer's name is read from timer-human; about.php reads"
        " timer-humant, so the name never shows.",
        "S5: the snapshot downloads show to SET_UP only, as snapshot.get needs.",
        "D80: the page drops the server's local URLs, the browser's user agent"
        " and its JavaScript version, and adds the setup health checks and the"
        " database.check results for SET_UP.",
    ),
    "fakeroster.php": (
        "D20: a 301 to /setup/fake-roster/, the query string kept.",
        "C56: needs SET_UP, as racer.fake does; fakeroster.php asks for"
        " CHECK_IN_RACERS, so its button failed for a role without SET_UP.",
        "C57: each group gets exactly the racers asked for; racer.fake adds or"
        " drops up to 2 per group at random.",
    ),
}

# Differences from PHP that wait on a quirk id or an owner decision, by the
# PHP page's name. The ticket's "## Still open" names each, and an entry moves
# to `ROUTE_DIVERGENCES` once it has an id.
UNDECIDED_DIVERGENCES: dict[str, tuple[str, ...]] = {}

# The PHP pages whose replacement behaves as PHP's does, layout aside.
LIKE_PHP: frozenset[str] = frozenset()


def action(
    name: str,
    *,
    permission: Requirement,
    failure_code: FailureCode = "notauthorized",
    session_write: bool = False,
    divergences: tuple[str, ...] = (),
):
    def register(handler):
        REGISTRY.register(
            Endpoint(
                "action",
                name,
                handler,
                permission,
                failure_code,
                session_write,
                divergences=divergences,
            )
        )
        return handler

    return register


def query(
    name: str,
    *,
    permission: Requirement,
    session_write: bool = False,
    get_writes: bool = False,
    divergences: tuple[str, ...] = (),
):
    def register(handler):
        REGISTRY.register(
            Endpoint(
                "query",
                name,
                handler,
                permission,
                session_write=session_write,
                get_writes=get_writes,
                divergences=divergences,
            )
        )
        return handler

    return register


class SessionWriteError(RuntimeError):
    pass


class ReadOnlySession(Mapping):
    """The session as a handler without `session_write` sees it.

    PHP closes the session for writing before such a handler runs. A write
    raises under `DEBUG` and in tests, and is dropped with a warning otherwise.
    """

    def __init__(self, session, endpoint_name: str):
        self._session = session
        self._endpoint_name = endpoint_name

    def __getitem__(self, key):
        return self._session[key]

    def __iter__(self):
        return iter(list(self._session.keys()))

    def __len__(self):
        return len(self._session.keys())

    def _refuse(self, *args, **kwargs):
        message = f"{self._endpoint_name} writes the session but lacks session_write"
        if settings.DEBUG or settings.LEGACY_SESSION_WRITES_RAISE:
            raise SessionWriteError(message)
        logger.warning(message)

    __setitem__ = __delitem__ = _refuse
    pop = setdefault = update = clear = flush = cycle_key = _refuse
    set_expiry = set_test_cookie = delete_test_cookie = _refuse
    save = create = delete = _refuse

    @property
    def session_key(self) -> str | None:
        return self._session.session_key

    def is_empty(self) -> bool:
        return self._session.is_empty()

    def test_cookie_worked(self) -> bool:
        return self._session.test_cookie_worked()

    def get_expiry_age(self, **kwargs) -> int:
        return self._session.get_expiry_age(**kwargs)

    def get_expiry_date(self, **kwargs):
        return self._session.get_expiry_date(**kwargs)

    def get_expire_at_browser_close(self) -> bool:
        return self._session.get_expire_at_browser_close()


@dataclass
class LegacyContext:
    args: dict
    # None only for a `.nodata` name with no active event.
    event: Event | None
    session: Mapping
    xml: XmlWriter
    out: dict = field(default_factory=dict)
    # The client's User-Agent, exactly as sent. Competing timers are told
    # apart by it, so a proxy must pass it through.
    user_agent: str = ""
    # The uploaded files of a multipart POST, by field name.
    files: Mapping = field(default_factory=dict)
    # The client's address, PHP's `$_SERVER['REMOTE_ADDR']`.
    remote_addr: str = ""

    def put(self, key: str, value) -> None:
        self.out[key] = value

    def success(self) -> None:
        self.put(
            "outcome", {"summary": "success", "code": "success", "description": ""}
        )

    def failure(self, code: str, description: str) -> None:
        self.put(
            "outcome", {"summary": "failure", "code": code, "description": description}
        )

    def resolve[M: Model](self, model: type[M], raw_id) -> M | None:
        """The active event's row of `model` with a client-supplied id, or None.

        A timer or kiosk can post an id from the event that was active a
        moment ago. Every id from a request goes through here, so it can only
        name a row of the active event.
        """
        try:
            pk = int(raw_id)
        except TypeError, ValueError:
            return None
        return model.objects.filter(event=self.event, pk=pk).first()
