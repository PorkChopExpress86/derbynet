"""The timer protocol: `timer-message`, which `derby-timer.jar` and the
browser timer post, and the race coordinator's timer controls (#32).

The timer's endpoints that PHP leaves open need `CONTROL_RACE` (#15, S21).
Every message and timer state change goes to the timer audit (D19).
"""

from accounts.permissions import Perm
from legacy_api.registry import LegacyContext, action, query
from racing.engine.phpcompat import php_intval, php_truthy
from racing.services import polls, timer


@action("timer-message", permission=Perm.TIMER_MESSAGE)
def timer_message(ctx: LegacyContext) -> None:
    body = timer.handle_message(ctx.event, ctx.args, ctx.user_agent)
    ctx.xml.start_response()
    ctx.xml.write(body)
    ctx.xml.end_response()


def _text(ctx: LegacyContext, key: str) -> str:
    value = ctx.args.get(key)
    return value if isinstance(value, str) else ""


@action("timer.assign-flag", permission=Perm.CONTROL_RACE)
def assign_flag(ctx: LegacyContext) -> None:
    timer.assign_flag(ctx.event, _text(ctx, "flag"), _text(ctx, "value"))
    ctx.success()


@action("timer.assign-port", permission=Perm.CONTROL_RACE)
def assign_port(ctx: LegacyContext) -> None:
    timer.assign_port(ctx.event, _text(ctx, "port"))
    ctx.success()


@action("timer.assign-device", permission=Perm.CONTROL_RACE)
def assign_device(ctx: LegacyContext) -> None:
    timer.assign_device(ctx.event, _text(ctx, "device"))
    ctx.success()


@action("timer.remote-start", permission=Perm.CONTROL_RACE)
def remote_start(ctx: LegacyContext) -> None:
    if timer.request_remote_start(ctx.event):
        ctx.success()
    else:
        ctx.failure("no-remote-start", "Remote start not available.")


@action("timer.test", permission=Perm.CONTROL_RACE)
def timer_test(ctx: LegacyContext) -> None:
    if "tt-mask" in ctx.args:
        timer.set_test_mask(ctx.event, php_intval(_text(ctx, "tt-mask")))
        ctx.success()
    elif "test-mode" in ctx.args:
        timer.set_test_mode(ctx.event, php_truthy(ctx.args["test-mode"]))
        ctx.success()
    else:
        ctx.failure("unknown-operation", "")


@query("timer.settings", permission=Perm.CONTROL_RACE)
def timer_settings(ctx: LegacyContext) -> None:
    for key, value in timer.settings_report(ctx.event).items():
        ctx.put(key, value)


@query("timer.log", permission=Perm.CONTROL_RACE)
def timer_log(ctx: LegacyContext) -> None:
    read = timer.read_log(ctx.event, max(php_intval(_text(ctx, "seek")), 0))
    if not read.found:
        ctx.failure("nolog", f"No log file {read.path}")
    else:
        if not timer.log_path_hidden():
            ctx.put("log-file-name", read.path)
        if read.data is not None:
            ctx.put("file-data", read.data)
    ctx.put("file-size", read.size)


@query("poll.timer.test", permission=Perm.CONTROL_RACE)
def poll_timer_test(ctx: LegacyContext) -> None:
    run = polls.running(ctx.event)
    ctx.put("current-heat", polls.current_heat(run))
    ctx.put("timer-state", polls.timer_state(ctx.event))
    ctx.put("timer-details", timer.details(ctx.event))
    ctx.put("tt", polls.timer_test(ctx.event))
