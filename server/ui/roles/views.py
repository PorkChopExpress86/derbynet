"""The roles page (#48, D79): each role with its permissions, which roles
keep a default password, and a password change. Roles are site-wide
(ADR-07), so a change applies to every event. Creating roles and changing
their permission bits are #15's to decide, so the page lists them only."""

from dataclasses import dataclass

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import Role
from accounts.services import (
    PasswordRefused,
    change_password,
    may_keep_no_password,
    permission_names,
    roles_with_default_password,
)
from ui.navigation import requires_page

guard = requires_page("roles")


@dataclass
class RoleView:
    role: Role
    permissions: list[str]
    default_password: bool
    no_password: bool
    may_keep_none: bool
    every_permission: bool
    saved: bool = False
    error: str = ""

    @property
    def label(self) -> str:
        return self.role.name or "Anonymous visitor"


def _label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _view(role: Role, defaults: set[str]) -> RoleView:
    return RoleView(
        role=role,
        permissions=[_label(n) for n in permission_names(role.permissions)],
        default_password=role.name in defaults,
        no_password=not role.password_hash,
        may_keep_none=may_keep_no_password(role.name),
        every_permission=role.permissions == -1,
    )


@require_GET
@guard
def page(request):
    defaults = set(roles_with_default_password())
    return render(
        request,
        "ui/roles/page.html",
        {"roles": [_view(r, defaults) for r in Role.objects.all()]},
    )


@require_POST
@guard
def password(request, name: str):
    """One role's new password (D79). The service refuses the anonymous role
    and an empty password, except for Timer, which may keep none."""
    raw = "" if request.POST.get("none") == "1" else request.POST.get("password", "")
    error = ""
    try:
        role = change_password(name, raw)
    except PasswordRefused as refused:
        if refused.role is None:
            raise Http404("No such role.") from None
        error, role = refused.description, refused.role
    view = _view(role, set(roles_with_default_password()))
    view.saved, view.error = not error, error
    return render(request, "ui/roles/card.html", {"rv": view})
