"""The message relay for WebRTC signaling between the replay page and the
remote camera, ported from `website/inc/messages.inc`.

A message with a recipient is deleted when that recipient reads it. A
broadcast has no recipient, reaches everyone who polls, and expires after a
second. A directed message nobody reads expires after 15 s.
"""

import json
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import JSONField, Value

from kiosks.models import SignalMessage

BROADCAST_LIFE = timedelta(seconds=1)
DIRECTED_LIFE = timedelta(seconds=15)
SIZE_LIMIT = 64 * 1024
"""The largest message, encoded, that `message.send` takes (#15). A WebRTC
offer with its candidates is a few kilobytes."""


class TooLarge(Exception):
    pass


def decoded(text: str):
    """PHP's `json_decode()`: the value, or None for text that is not JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _purge(event, now: datetime) -> None:
    """`purge_stale_messages()`."""
    SignalMessage.objects.filter(event=event, expires_at__lt=now).delete()


def send(event, recipient: str, payload, now: datetime) -> None:
    """`send_message()`. Raises `TooLarge` past `SIZE_LIMIT`."""
    if len(json.dumps(payload)) > SIZE_LIMIT:
        raise TooLarge
    life = DIRECTED_LIFE if recipient else BROADCAST_LIFE
    if payload is None:
        # JSON null, as PHP's json_decode() of text that is not JSON.
        payload = Value(None, JSONField())
    with transaction.atomic():
        SignalMessage.objects.create(
            event=event, recipient=recipient, expires_at=now + life, payload=payload
        )
        _purge(event, now)


def retrieve(event, recipient: str | None, now: datetime) -> list:
    """`retrieve_messages()`: the recipient's messages and the broadcasts, in
    order of expiry. The recipient's own are deleted in the same
    transaction, so two polls never both get one."""
    with transaction.atomic():
        _purge(event, now)
        rows = list(
            SignalMessage.objects.select_for_update()
            .filter(event=event, recipient__in=[recipient or "", ""])
            .order_by("expires_at", "pk")
        )
        mine = [row.pk for row in rows if row.recipient]
        SignalMessage.objects.filter(pk__in=mine).delete()
    return [row.payload for row in rows]
