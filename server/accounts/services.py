"""Role checks that pages need beyond a login (#15, D16)."""

from django.contrib.auth.hashers import check_password

from accounts.defaults import DEFAULT_ROLES
from accounts.models import Role

_DEFAULT_PASSWORDS = {name: pw for name, pw, _, _ in DEFAULT_ROLES if pw}

# A hash check costs a few hundred milliseconds by design, and every manager
# page asks. A role's hash changes with its password, so a result stays true
# for as long as the hash it was computed from is the stored one.
_checked: dict[tuple[str, str], bool] = {}


def _is_default(name: str, password_hash: str) -> bool:
    key = (name, password_hash)
    if key not in _checked:
        _checked[key] = check_password(_DEFAULT_PASSWORDS[name], password_hash)
    return _checked[key]


def roles_with_default_password() -> list[str]:
    """The roles, in login-page order, whose password is still the one a new
    install seeds (`accounts.defaults`). A role with no password, such as
    Timer, was seeded without one and is never listed."""
    roles = Role.objects.filter(name__in=_DEFAULT_PASSWORDS).exclude(password_hash="")
    return [
        name
        for name, password_hash in roles.values_list("name", "password_hash")
        if _is_default(name, password_hash)
    ]
