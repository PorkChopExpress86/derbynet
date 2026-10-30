"""The replay station's endpoints (#34): the WebRTC message relay
(`message.send`, `message.retrieve`), the replay command queue
(`replay.message`), and `replay.test` and `replay.trigger`.

The replay page and the remote camera log in once during setup, as a role
that holds `CONTROL_RACE` (D5, D9, D16). The Photo role cannot run replay.
None of these is written to the action log (#17).
"""

from django.utils import timezone

from accounts.permissions import Perm
from kiosks import messages
from legacy_api.registry import LegacyContext, action
from racing.services import replay

REPLAY_LOGIN = ("D16: needs CONTROL_RACE, the replay login; PHP takes it from anyone.",)


def _text(ctx: LegacyContext, name: str) -> str | None:
    value = ctx.args.get(name)
    return value if isinstance(value, str) else None


def _payload(ctx: LegacyContext) -> tuple[str, object] | None:
    """`message[...]` as a PHP form array, whose `recipient` addresses it, or
    the unused form with `recipient` and `message` as text, which PHP stores
    as sent and decodes when read. None with no message."""
    message = ctx.args.get("message")
    recipient = _text(ctx, "recipient")
    if recipient is not None:
        return recipient, messages.decoded(message if isinstance(message, str) else "")
    if isinstance(message, dict):
        to = message.get("recipient")
        return (to if isinstance(to, str) else ""), message
    return None


@action(
    "message.send",
    permission=Perm.CONTROL_RACE,
    divergences=REPLAY_LOGIN
    + ("D16: a message over 64 KiB encoded fails with too-large.",),
)
def message_send(ctx: LegacyContext) -> None:
    found = _payload(ctx)
    if found is not None:
        try:
            messages.send(ctx.event, *found, timezone.now())
        except messages.TooLarge:
            ctx.failure("too-large", "The message is too large.")
            return
    ctx.success()


@action("message.retrieve", permission=Perm.CONTROL_RACE, divergences=REPLAY_LOGIN)
def message_retrieve(ctx: LegacyContext) -> None:
    found = messages.retrieve(ctx.event, _text(ctx, "recipient"), timezone.now())
    ctx.put("messages", found)
    ctx.success()


@action("replay.message", permission=Perm.CONTROL_RACE, divergences=REPLAY_LOGIN)
def replay_message(ctx: LegacyContext) -> None:
    """`status` is the replay client's state, `finished-replay` unused."""
    commands = replay.poll(ctx.event.id, _text(ctx, "status") or "", timezone.now())
    ctx.put("replay", commands)
    ctx.success()


@action("replay.test", permission=Perm.CONTROL_RACE)
def replay_test(ctx: LegacyContext) -> None:
    replay.send_test(ctx.event.id)
    ctx.success()


@action(
    "replay.trigger",
    permission=Perm.CONTROL_RACE,
    divergences=(
        "C21: a refusal is the JSON notauthorized; PHP's not_authorized_body()"
        " writes XML into the JSON reply.",
    ),
)
def replay_trigger(ctx: LegacyContext) -> None:
    replay.trigger(ctx.event.id)
    ctx.success()
