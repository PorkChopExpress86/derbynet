"""Logging in by role: `role.login`, `role.list`, and the `login` and `roles`
stubs that tell very old derby-timer.jar builds to upgrade (#15, #16).

All four stay anonymous, because a client reads the role list and logs in
before it holds any role (D23).
"""

from django.utils import timezone

from accounts.models import Role
from accounts.permissions import Access, Perm, log_in
from legacy_api.registry import LegacyContext, action, query

# The PHP files `action.login.inc` and `query.roles.inc` are this text.
LOGIN_STUB = """<action-response action="login">
  <failure code="version">Please upgrade your derby-timer.jar
                          (should be using role.login to login)</failure>
</action-response>
"""
ROLES_STUB = """<action-response action="login">
  <failure code="version">Please upgrade your derby-timer.jar</failure>
  <role timer_message="1">Please upgrade derby-timer.jar</role>
</action-response>
"""


def timecheck() -> str:
    """PHP's `date(DATE_ISO8601)`. The Pi photo scripts set their clocks from
    it."""
    return timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S%z")


@action("role.login", permission=Access.ANONYMOUS, session_write=True)
def role_login(ctx: LegacyContext) -> None:
    # With no name, PHP ignores the password and logs out.
    name = ctx.args.get("name", "")
    password = ctx.args.get("password", "") if name else ""
    found = Role.objects.named(name)
    if found is None:
        # PHP interpolates the failed lookup, which is empty.
        ctx.failure("role", "No such role: ")
        return
    if not (isinstance(password, str) and found.check_password(password)):
        ctx.failure("login", "Incorrect password")
        return
    log_in(ctx.session, found)
    ctx.put("timecheck", timecheck())
    # PHP names the role only for a login with a password.
    if password:
        ctx.put("role", found.name)
    ctx.success()


@query("role.list", permission=Access.ANONYMOUS)
def role_list(ctx: LegacyContext) -> None:
    ctx.put(
        "roles",
        [
            {"name": r.name, "timer-message": r.permissions & Perm.TIMER_MESSAGE}
            for r in Role.objects.all()
        ],
    )


@action("login", permission=Access.ANONYMOUS)
def login_stub(ctx: LegacyContext) -> None:
    ctx.xml.write(LOGIN_STUB)


@query("roles", permission=Access.ANONYMOUS)
def roles_stub(ctx: LegacyContext) -> None:
    ctx.xml.write(ROLES_STUB)
