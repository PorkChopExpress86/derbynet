"""The endpoints that exist for the shell suites only (#63).

`DERBYNET_ENABLE_TEST_ENDPOINTS=1` turns them on. With it off they are not
registered, so a real race answers `unrecognized` for them. `sync()` brings the
registry in line with the setting, at import and when a test changes it.
"""

from django.conf import settings

from accounts.permissions import Perm
from core import slides
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


def _endpoints() -> list[Endpoint]:
    return [Endpoint("action", "test.setup", seed_slide, Perm.SET_UP)]


def sync() -> None:
    for endpoint in _endpoints():
        registered = REGISTRY.get(endpoint.kind, endpoint.name)
        if settings.ENABLE_TEST_ENDPOINTS and registered is None:
            REGISTRY.register(endpoint)
        elif not settings.ENABLE_TEST_ENDPOINTS and registered is not None:
            REGISTRY.unregister(registered)
