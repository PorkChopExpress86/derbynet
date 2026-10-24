"""Permission bits and the session keys that carry them (#15, ADR-16).

The bit values are those of `website/inc/permissions.inc`. A login stores the
role name and its bits in the session. #16 writes those keys, and gives the
anonymous role and the first-run setup their bits in `granted()`. Everything
that checks a permission reads the session through `granted()` and `role()`.
"""

from collections.abc import Mapping
from enum import Enum, IntFlag


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
    LOGGED_IN = "logged-in"


# A permission requirement is satisfied by any one of its bits, as PHP's
# have_permission() is.
Requirement = Perm | Access

SESSION_ROLE = "role"
SESSION_PERMISSIONS = "permissions"


def granted(session: Mapping) -> int:
    """The session's permission bits. -1 holds every bit."""
    return int(session.get(SESSION_PERMISSIONS, 0))


def role(session: Mapping) -> str | None:
    """The name of the role the session logged in as, or None."""
    return session.get(SESSION_ROLE) or None


def allows(session: Mapping, requirement: Requirement) -> bool:
    match requirement:
        case Access.ANONYMOUS:
            return True
        case Access.LOGGED_IN:
            return role(session) is not None
        case Perm():
            return granted(session) & requirement != 0
