"""The message relay for WebRTC signaling between the replay page and the
remote camera, ported from `website/inc/messages.inc`.

A message with a recipient is deleted when that recipient reads it. A
broadcast has no recipient, reaches everyone who polls, and expires after a
second. A directed message nobody reads expires after 15 s. Both count whole
seconds, as PHP does (`messages.inc:58-62`, `messages.inc:95-98`, `_purge`).
"""

import json
import math
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import JSONField, Value

from kiosks.models import SignalMessage
from racing.engine.phpcompat import from_php_time, php_time

BROADCAST_LIFE = timedelta(seconds=1)
DIRECTED_LIFE = timedelta(seconds=15)
SIZE_LIMIT = 64 * 1024
"""The largest message, encoded, that `message.send` takes (#15). A WebRTC
offer with its candidates is a few kilobytes."""


class TooLarge(Exception):
    pass


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def decoded(text: str):
    """PHP's `json_decode()`: the value, or None for text that is not JSON.
    A number past a float's range, such as `1e999`, is None too: PHP decodes
    it as INF, which no JSON column holds and `json_encode()` refuses, so
    PHP's `message.retrieve` could not encode its reply either (inferred)."""
    try:
        return json.loads(text, parse_float=_finite)
    except ValueError:
        return None


def _purge(event, now: datetime) -> None:
    """`purge_stale_messages()`: PHP stores `time() + $timeout`
    (`messages.inc:58-62`) and deletes at `expiration < time()`
    (`messages.inc:95-98`), in whole seconds (#360). The port stores
    `now + life` with its fraction, whose second is PHP's, so a message
    goes once `php_time(now)` passes that second: the bound is the first
    moment of `php_time(now)`. A broadcast sent 0.6 s past a whole second
    lives until 2 s past that second, 1.4 s after it was sent
    (`test_a_broadcast_expires_in_whole_seconds`)."""
    SignalMessage.objects.filter(
        event=event, expires_at__lt=from_php_time(php_time(now))
    ).delete()


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
