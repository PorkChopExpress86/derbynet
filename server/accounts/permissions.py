"""Permission bits and the session keys that carry them (#15, ADR-16).

The bit values are those of `website/inc/permissions.inc`. A login stores the
role name and its bits in the session. A session with no stored bits holds the
anonymous role's, read from the database on each request, and every session
holds `SET_UP` while no event exists. Everything that checks a permission goes
through `granted()` and `role()`, and only `log_in()`, `log_out()` and
`add_bits()` write the keys.
"""

from enum import Enum, IntFlag

from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from accounts.models import Role
from core.models import Event


class Perm(IntFlag):
    VIEW_RACE_RESULTS = 1
    VIEW_AWARDS = 2
    CHECK_IN_RACERS = 4
    REVERT_CHECK_IN = 8
    REGISTER_NEW_RACER = 32
    EDIT_RACER = 64
    ASSIGN_RACER_IMAGE = 128
    JUDGING = 256
    CONTROL_RACE = 512
    PRESENT_AWARDS = 1024
    EDIT_AWARDS = 2048
    TIMER_MESSAGE = 4096
    PHOTO_UPLOAD = 8192
    SET_UP = 32768
    ADMINISTRATION = 65536


class Access(Enum):
    """A requirement that is not a permission bit."""

    ANONYMOUS = "anonymous"
    # Logged in as a role that has a password. Anyone can log in as `Timer`,
    # whose password is empty and whose name `role.list` publishes, so that
    # login proves nothing. The timer's handlers require `TIMER_MESSAGE`.
    LOGGED_IN = "logged-in"


# A permission requirement is satisfied by any one of its bits, as PHP's
# have_permission() is.
Requirement = Perm | Access

SESSION_ROLE = "role"
SESSION_PERMISSIONS = "permissions"


def _stored_bits(session) -> int:
    """The session's own bits, without the first-run `SET_UP`. With no Role
    rows the anonymous session holds none, where PHP grants every bit (S32)."""
    if SESSION_PERMISSIONS in session:
        return int(session[SESSION_PERMISSIONS])
    anonymous = Role.objects.filter(name="").values_list("permissions", flat=True)
    return anonymous.first() or 0


def first_run() -> bool:
    return not Event.objects.exists()


def granted(request: HttpRequest) -> int:
    """The request's permission bits. -1 holds every bit."""
    bits = _stored_bits(request.session)
    return bits | int(Perm.SET_UP) if first_run() else bits


def role(request: HttpRequest) -> str | None:
    """The name of the role the session logged in as, or None."""
    return request.session.get(SESSION_ROLE) or None


def allows(request: HttpRequest, requirement: Requirement) -> bool:
    match requirement:
        case Access.ANONYMOUS:
            return True
        case Access.LOGGED_IN:
            name = role(request)
            return (
                name is not None
                and Role.objects.filter(name=name).exclude(password_hash="").exists()
            )
        case Perm():
            return granted(request) & requirement != 0


# Each writer issues a new session id, so an id fixed before a change of
# privilege is worthless after it (S31).


def log_in(session: SessionBase, logged_in: Role) -> None:
    """The anonymous role logs out."""
    if not logged_in.name:
        log_out(session)
        return
    session.cycle_key()
    session[SESSION_ROLE] = logged_in.name
    session[SESSION_PERMISSIONS] = logged_in.permissions


def log_out(session: SessionBase) -> None:
    """Forget the whole session, as a shared check-in phone needs: the next
    user must not inherit the barcode mode or any other key."""
    session.flush()


def add_bits(session: SessionBase, bits: Perm) -> None:
    """OR `bits` into the session's role, as a mobile check-in link does."""
    session.cycle_key()
    session[SESSION_PERMISSIONS] = _stored_bits(session) | int(bits)
