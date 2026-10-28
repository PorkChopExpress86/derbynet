"""The endpoints for the shell suites (#63): `test.setup`, and `database.check`
in its anonymous form.

`DERBYNET_ENABLE_TEST_ENDPOINTS=1` turns them on. With it off they are not
registered, so a real race answers `unrecognized` for them. `sync()` brings the
registry in line with the setting, at import and when a test changes it.
"""

from django.conf import settings

from accounts.permissions import Access, Perm
from core import slides
from core.invariants import check_invariants
from legacy_api.registry import REGISTRY, Endpoint, LegacyContext


def seed_slide(ctx: LegacyContext) -> None:
    """`test.setup`: copy the emblem into the slides folder as `slide`.

    PHP calls its two-argument `json_failure()` with one on both error paths,
    which throws on PHP 8 (C17), and answers any caller (S24).
    """
    subdir = ctx.args.get("subdir")
    slide = ctx.args.get("slide")
    if (subdir is not None and not isinstance(subdir, str)) or (
        slide is not None and not isinstance(slide, str)
    ):
        ctx.failure("invalid-name", "Invalid subdir or slide name")
        return
    try:
        folder = slides.slides_dir(ctx.event, subdir)
    except slides.InvalidName:
        ctx.failure("invalid-subdir", "Invalid subdir")
        return
    if slide is None:
        ctx.failure("unrecognized-operation", "Operation not recognized")
        return
    ctx.put("slides_dir", str(folder))
    try:
        slides.install_test_slide(folder, slide)
    except slides.InvalidName:
        ctx.failure("invalid-slide", "Invalid slide name")
        return
    ctx.success()


def check_database(ctx: LegacyContext) -> None:
    """`database.check`: report the invariants the database does not enforce.

    PHP names a tag for each violated check and lists its rows, then fails with
    `corrupt`. The tags are those of `core.invariants`, plus `cross-event`.
    """
    violations = check_invariants(ctx.event)
    for tag, rows in violations.items():
        ctx.put(tag, rows)
    if violations:
        ctx.failure("corrupt", "Discrepancies detected")
    else:
        ctx.success()


# The endpoints `sync()` manages, as (kind, name).
MANAGED = (("query", "database.check"), ("action", "test.setup"))


def _wanted() -> list[Endpoint]:
    """`database.check` answers anyone while the test flag is on, because
    `testing/common.sh` calls it before every POST, logged in or not. It
    reports the database's faults, so a real race requires `SET_UP` (S26)."""
    enabled = settings.ENABLE_TEST_ENDPOINTS
    wanted = [
        Endpoint(
            "query",
            "database.check",
            check_database,
            Access.ANONYMOUS if enabled else Perm.SET_UP,
        )
    ]
    if enabled:
        wanted.append(Endpoint("action", "test.setup", seed_slide, Perm.SET_UP))
    return wanted


def sync() -> None:
    for kind, name in MANAGED:
        registered = REGISTRY.get(kind, name)
        if registered is not None:
            REGISTRY.unregister(registered)
    for endpoint in _wanted():
        REGISTRY.register(endpoint)
