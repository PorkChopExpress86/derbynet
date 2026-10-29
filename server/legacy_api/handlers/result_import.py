"""`result.import`: enter one result line from an old event's results (#36)."""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action
from racing.services import result_import


@action("result.import", permission=Perm.SET_UP)
def result_import_line(ctx: LegacyContext) -> None:
    failure = result_import.import_result(ctx.event, ctx.args)
    if failure is None:
        ctx.success()
    else:
        ctx.failure(failure.code, failure.description)
