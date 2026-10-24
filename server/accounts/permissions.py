"""Permission bits and the session keys that carry them (#15, ADR-16).

The bit values are those of `website/inc/permissions.inc`. A login stores the
role name and its bits in the session. #16 writes those keys, and gives the
anonymous role's stored bits and the first-run `SET_UP` in `granted()`, which
takes the request so it can read them. Everything that checks a permission
goes through `granted()` and `role()`.
"""

from enum import Enum, IntFlag

from django.http import HttpRequest


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


def granted(request: HttpRequest) -> int:
    """The request's permission bits. -1 holds every bit."""
    return int(request.session.get(SESSION_PERMISSIONS, 0))


def role(request: HttpRequest) -> str | None:
    """The name of the role the session logged in as, or None."""
    return request.session.get(SESSION_ROLE) or None


def allows(request: HttpRequest, requirement: Requirement) -> bool:
    match requirement:
        case Access.ANONYMOUS:
            return True
        case Access.LOGGED_IN:
            return role(request) is not None
        case Perm():
            return granted(request) & requirement != 0
