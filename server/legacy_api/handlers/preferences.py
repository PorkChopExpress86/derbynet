"""`preferences.upload`: apply a preferences file (#36)."""

from accounts.permissions import Perm
from core import setup_details
from legacy_api.registry import LegacyContext, action
from roster.services import preferences


@action("preferences.upload", permission=Perm.SET_UP)
def preferences_upload(ctx: LegacyContext) -> None:
    upload = ctx.files.get("prefs")
    content = upload.read().decode("utf-8", errors="replace") if upload else ""
    failure = preferences.apply(ctx.event, content)
    if failure is None:
        ctx.success()
    else:
        ctx.failure(failure.code, failure.description)
    ctx.put("details", setup_details.build(ctx.event))
