from functools import cached_property

from accounts.permissions import Perm, allows, role
from accounts.services import roles_with_default_password
from ui import navigation
from ui.version import build_version


class Shell:
    """What `ui/base.html` draws around a page, computed only when a template
    reads it."""

    def __init__(self, request):
        self.request = request

    @cached_property
    def role(self) -> str | None:
        return role(self.request)

    @cached_property
    def entry(self) -> navigation.NavEntry | None:
        return getattr(self.request, "nav_entry", None)

    @cached_property
    def sections(self):
        return navigation.by_section(navigation.menu_entries(self.request))

    @cached_property
    def bottom_nav(self):
        return navigation.bottom_nav(self.request)

    @cached_property
    def more_sections(self):
        """The drawer behind the bottom nav's "More": every menu entry the
        bottom nav leaves out."""
        shown = set(self.bottom_nav or ())
        entries = [e for e in navigation.menu_entries(self.request) if e not in shown]
        return navigation.by_section(entries)

    @cached_property
    def default_password_roles(self) -> list[str]:
        """Shown to logged-in roles only (D16)."""
        return roles_with_default_password() if self.role else []

    @cached_property
    def can_change_passwords(self) -> bool:
        return navigation.can_open(self.request, navigation.entry("roles"))

    @cached_property
    def set_up(self) -> bool:
        return allows(self.request, Perm.SET_UP)

    @property
    def version(self) -> str:
        return build_version()


def shell(request):
    return {"shell": Shell(request)}
