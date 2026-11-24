"""Role checks that pages need beyond a login (#15, D16)."""

from accounts.defaults import DEFAULT_ROLES
from accounts.models import Role
from accounts.permissions import Perm


def roles_with_default_password() -> list[str]:
    """The roles, in login-page order, whose password is still the one a new
    install seeds (`accounts.defaults`). A role with no password, such as
    Timer, was seeded without one and is never listed. The answer is stored
    (`Role.keeps_seeded_password`), so no page hashes for it (#441)."""
    roles = Role.objects.filter(keeps_seeded_password=True).exclude(password_hash="")
    return list(roles.values_list("name", flat=True))


# The roles a new install seeds with no password, which may keep none (D79):
# Timer, whose derby-timer.jar logs in with an empty password.
_SEEDED_WITHOUT_PASSWORD = frozenset(
    name for name, pw, _, _ in DEFAULT_ROLES if name and not pw
)


class PasswordRefused(Exception):
    """`role` is the role the change was refused for, or None when no role
    has the name."""

    def __init__(self, code: str, description: str, role: Role | None = None):
        super().__init__(description)
        self.code = code
        self.description = description
        self.role = role


def may_keep_no_password(name: str) -> bool:
    return name in _SEEDED_WITHOUT_PASSWORD


def change_password(name: str, raw: str) -> Role:
    """Set the password of the role called `name` (D79). Roles are site-wide,
    so the change applies to every event. The anonymous role has no login and
    is refused, and so is an empty password, except for a role seeded with
    none (Timer)."""
    found = Role.objects.named(name)
    if found is None:
        raise PasswordRefused("no-such-role", "There is no such role.")
    if not found.name:
        raise PasswordRefused(
            "anonymous", "The anonymous role has no password to change.", found
        )
    if not isinstance(raw, str) or "\x00" in raw:
        raise PasswordRefused("bad-password", "Type the new password.", found)
    if raw == "" and not may_keep_no_password(found.name):
        raise PasswordRefused("empty-password", "Type the new password.", found)
    found.set_password(raw)
    Role.objects.filter(pk=found.pk).update(
        password_hash=found.password_hash,
        keeps_seeded_password=found.keeps_seeded_password,
    )
    return found


def permission_names(bits: int) -> list[str]:
    """The names of the permission bits a role holds, in bit order. -1 holds
    every bit."""
    return [p.name for p in Perm if bits == -1 or bits & p]
