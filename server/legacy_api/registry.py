"""The legacy `/action.php` endpoints and what a handler sees (#17).

A handler is `def handler(ctx: LegacyContext) -> None`, registered under its
exact PHP name with `@action(...)` or `@query(...)`. It reads `ctx.args`,
calls services, and writes the response through `ctx` the way the PHP
handlers write `$json_out` or echo XML: the last outcome wins.
"""

import logging
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

from django.conf import settings
from django.db.models import Model

from accounts.permissions import Requirement
from core.models import Event
from legacy_api.formats import Format, format_of
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


_BALLOTING = "Balloting moved to the backlog (D7)."

# Every PHP endpoint deferred to a backlog ticket. A name here is never
# registered, so the dispatcher answers it as it answers any unknown name.
DEFERRED: tuple[Deferred, ...] = (
    Deferred("action", "ballot.open", "#54", _BALLOTING),
    Deferred("query", "ballot.get", "#54", _BALLOTING),
    Deferred("action", "vote.cast", "#54", _BALLOTING),
    Deferred("query", "ballot.results", "#54", _BALLOTING),
)


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


ANY = object()  # A ParityPath value that matches any value.


@dataclass(frozen=True)
class ParityPath:
    """Where in an endpoint's reply the parity gate (#37) lets Django differ
    from PHP, under one of the endpoint's `divergences`.

    `endpoint` is `action <name>` or `query <name>`. `path` is a reply path as
    `tools/transcript_diff.py` prints it (`$.current-heat.classid`,
    `/action-response/heat-ready/@heat`), where `*` stands for any run of
    characters and `[*]` for any list index. `php` and `django` are the
    values the divergence gives each server there, after the differ's
    normalizing (`MISSING` from the differ for a key one lacks), or `ANY`.
    `divergence` is the id that starts one of the endpoint's `divergences`
    (`R3`); the guard in legacy_api/tests/test_registry.py checks it does."""

    endpoint: str
    path: str
    divergence: str
    php: object = ANY
    django: object = ANY

    def covers(self, endpoint: str, path: str, php=ANY, django=ANY) -> bool:
        return (
            endpoint == self.endpoint
            and _path_pattern(self.path).fullmatch(path) is not None
            and _matches(self.php, php)
            and _matches(self.django, django)
        )


def _matches(expected, value) -> bool:
    if expected is ANY or value is ANY:
        return True
    # 0 == False and 1 == True in Python; JSON tells them apart.
    return type(expected) is type(value) and expected == value


def _path_pattern(pattern: str) -> re.Pattern:
    parts = re.split(r"(\[\*\]|\*)", pattern)
    wild = {"[*]": r"\[\d+\]", "*": ".*"}
    return re.compile("".join(wild.get(part, re.escape(part)) for part in parts))


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

# Each reply path a recorded divergence changes, as the parity gate saw it,
# with the values the divergence names. Add one only for a difference that an
# existing divergence of that endpoint already explains; any other difference
# fails the gate.
PARITY_PATHS: tuple[ParityPath, ...] = (
    *(
        ParityPath(endpoint, f"$.current-heat.{key}", "R3", php=-1, django=None)
        for endpoint in _CURRENT_HEAT
        for key in ("classid", "heat")
    ),
    # PHP repeats each column under its position; MISSING is the differ's.
    ParityPath("query poll.results", '$.results[*]["*"]', "R9"),
    *(
        ParityPath(endpoint, f"$.awards[*].{key}", "A1", php=None, django=0)
        for endpoint in ("action award.edit", "query award.list")
        for key in ("classid", "rankid")
    ),
    *(
        ParityPath(endpoint, "$.current-scene", "C19", php="")
        for endpoint in _KIOSK_LISTS
    ),
    *(
        ParityPath(endpoint, "$.kiosks[*].parameters", "C20", php=None)
        for endpoint in _KIOSK_LISTS
    ),
)


def divergence_text(endpoint: str, divergence: str) -> str | None:
    """The endpoint's divergence entry whose ids include `divergence`."""
    kind, _, name = endpoint.partition(" ")
    found = REGISTRY.get(kind, name)
    for text in found.divergences if found else ():
        ids = text.split(": ", 1)[0].split(", ")
        if divergence in ids:
            return text
    return None


def allowed_by(endpoint: str, path: str, php=ANY, django=ANY) -> str | None:
    """The divergence that lets Django's reply differ from PHP's at `path`,
    where PHP gave `php` and Django `django`, or None. The parity gate fails
    on a difference this doesn't allow."""
    for allowed in PARITY_PATHS:
        if allowed.covers(endpoint, path, php, django):
            return divergence_text(endpoint, allowed.divergence)
    return None


# The legacy pages outside `/action.php` that differ from PHP on purpose, by
# path, in the form of `Endpoint.divergences`.
ROUTE_DIVERGENCES: dict[str, tuple[str, ...]] = {
    "photo.php/info": (
        "S8: removed, and answers 404; PHP serves the file any setting names, or a"
        " path from the URL.",
    ),
}


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
