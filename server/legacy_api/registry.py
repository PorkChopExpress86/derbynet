"""The legacy `/action.php` endpoints and what a handler sees (#17).

A handler is `def handler(ctx: LegacyContext) -> None`, registered under its
exact PHP name with `@action(...)` or `@query(...)`. It reads `ctx.args`,
calls services, and writes the response through `ctx` the way the PHP
handlers write `$json_out` or echo XML: the last outcome wins.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from django.conf import settings
from django.db.models import Model

from accounts.permissions import Requirement
from core.models import Event
from legacy_api.xml import XmlWriter

logger = logging.getLogger(__name__)

Kind = Literal["action", "query"]
FailureCode = Literal["notauthorized", "permission"]


class Format(StrEnum):
    JSON = "json"
    XML = "xml"


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


def format_of(name: str) -> Format:
    """PHP's rule. `login` and `roles` are in XML for older derby-timer.jar."""
    if name in ("timer-message", "login", "roles") or "snapshot" in name:
        return Format.XML
    return Format.JSON


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
    # gate (#37) allows exactly these.
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
