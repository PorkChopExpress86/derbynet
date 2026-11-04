"""A poll answers 204 only when what it would render is unchanged (#45
retro). #45's presenter poll kept answering 204 while the presented award
went to another racer: its signature covered the award's key and reveal
flag, not its winner.

For each poll that answers 204 on a matching `sig`, the test builds a race
day, reads the poll, and then changes one stored field at a time, in a
savepoint it rolls back: every editable column of the first and last row of
each race model. When the poll answers 204 to the old signature, its
rendering must be the same as before the change. A field the poll never
renders passes either way, so the check holds whatever the poll reads.

A new poll that sends `sig` is added to POLLS; the first test finds one
that is not.
"""

import datetime
import re

import pytest
from django.apps import apps
from django.db import IntegrityError, models, transaction
from django.test import Client
from django.utils import timezone

from core.models import Event
from ui.tests.roles import PASSWORDS
from ui.tests.test_query_growth import URLS, race_day

pytestmark = pytest.mark.django_db

# Each poll that answers 204 while the `sig` it was sent holds.
POLLS = [
    "/dashboard/live/",
    "/checkin/list/",
    "/judging/board/",
    "/awards/present/current/",
    "/kiosks/board/",
    "/results/rounds/",
    "/timer/status/",
    "/timer/test/live/",
]
APPS = ("core", "roster", "racing", "awards", "kiosks")
SIG = re.compile(r'<input type="hidden"(?: id="[\w-]+")? name="sig" value="([^"]*)">')
# The dashboard also carries its signature as an attribute.
SIG_ATTRIBUTE = re.compile(r' data-signature="[^"]*"')
# Bookkeeping a page never shows.
SKIP = {"id", "event", "created", "modified", "updated"}


class _Undo(Exception):
    pass


def _changed(field, value, rows):
    """Another valid value for the field, or None to leave it."""
    if field.choices:
        others = [c for c, _ in field.flatchoices if c != value]
        return others[0] if others else None
    if isinstance(field, models.ForeignKey):
        others = field.related_model.objects.exclude(pk=value)
        if hasattr(field.related_model, "event"):
            others = others.filter(event=rows.first().event)
        other = others.values_list("pk", flat=True).first()
        return other
    if isinstance(field, models.BooleanField):
        return not value
    if isinstance(field, models.IntegerField | models.FloatField | models.DecimalField):
        return (value or 0) + 1
    if isinstance(field, models.CharField | models.TextField):
        text = f"{value or ''}Z"
        return text[-field.max_length :] if field.max_length else text
    if isinstance(field, models.DateTimeField | models.DateField):
        return (value or datetime.datetime(2026, 1, 1)) + datetime.timedelta(days=1)
    return None


def _mutations(event):
    for model in apps.get_models():
        if model._meta.app_label not in APPS or model is Event:
            continue
        rows = model.objects.all()
        if any(f.name == "event" for f in model._meta.fields):
            rows = rows.filter(event=event)
        pks = list(rows.order_by("pk").values_list("pk", flat=True))
        for pk in {pks[0], pks[-1]} if pks else ():
            row = model.objects.filter(pk=pk)
            for field in model._meta.concrete_fields:
                if field.primary_key or field.name in SKIP:
                    continue
                if isinstance(field, models.JSONField | models.BinaryField):
                    continue
                value = row.values_list(field.attname, flat=True)[0]
                new = _changed(field, value, rows)
                if new is not None and new != value:
                    yield f"{model.__name__}#{pk}.{field.attname}", row, field, new


def _render(client, url, sig=None):
    """(status, the rendering less its signature, the signature)."""
    params = {"sig": sig} if sig is not None else {}
    response = client.get(url, params, HTTP_HX_REQUEST="true")
    if response.status_code != 200:
        return response.status_code, None, None
    html = response.content.decode()
    found = SIG.search(html)
    return 200, SIG_ATTRIBUTE.sub("", SIG.sub("", html)), found and found.group(1)


@pytest.fixture
def coordinator(event):
    client = Client(raise_request_exception=False)
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]}
    )
    assert response.status_code == 302
    return client


def test_every_poll_that_answers_204_is_checked(coordinator, event):
    race_day(event, [("G1", 3)], race=True)
    answering = set()
    for url in URLS:
        status, _, sig = _render(coordinator, url)
        if sig is not None and _render(coordinator, url, sig)[0] == 204:
            answering.add(url)
    assert answering == set(POLLS)


@pytest.fixture
def frozen_clock(monkeypatch):
    """The clock stops, so only the stored field the test changes moves the
    rendering. The kiosk board shows a kiosk as quiet 5 s after its last
    contact: a run past 5 s on a busy machine crossed it between a 204 and
    the render after it, and blamed whichever field was changed then (8 of
    25 on Postgres under load)."""
    frozen = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: frozen)
    return frozen


@pytest.mark.parametrize("url", POLLS)
def test_a_204_means_the_poll_would_render_the_same(
    coordinator, event, url, frozen_clock
):
    race_day(event, [("G1", 3), ("G2", 3)], race=True)
    status, before, sig = _render(coordinator, url)
    assert status == 200
    assert _render(coordinator, url) == (200, before, sig), "the poll is not stable"
    assert _render(coordinator, url, sig)[0] == 204, "the poll never answers 204"

    stale, shown = [], 0
    for name, row, field, new in _mutations(event):
        try:
            with transaction.atomic():
                row.update(**{field.attname: new})
                status, polled, _ = _render(coordinator, url, sig)
                if status == 204:
                    status, after, _ = _render(coordinator, url)
                    if status == 200 and after != before:
                        stale.append(name)
                elif status == 200 and polled != before:
                    shown += 1
                raise _Undo
        except _Undo, IntegrityError:
            pass
    assert stale == [], stale
    assert shown > 0, "no change showed: the test checks nothing"


def _heartbeats(event):
    """Every row of the event with a `last_contact`: a kiosk, the timer and
    the replay app write it every few seconds while nothing else changes."""
    for model in apps.get_models():
        if model._meta.app_label in APPS and "last_contact" in {
            f.name for f in model._meta.concrete_fields
        }:
            yield model.objects.filter(event=event)


# Polls that answer 200 at a heartbeat on purpose, with the reason. The race
# dashboard shows the seconds since the timer's and the replay app's last call
# at every age, as PHP's coordinator page does, so a call resets the count
# (`ui.dashboard.state._material`). Its controls sync with `#live:replace`.
SWAPS_AT_A_HEARTBEAT = {"/dashboard/live/"}


@pytest.mark.parametrize("url", POLLS)
def test_a_heartbeat_leaves_the_poll_at_204(coordinator, event, url):
    """A poll that answers 200 at every heartbeat swaps its region every few
    seconds, which costs a tap that lands mid-swap (#46's kiosk board signed
    each kiosk's contact time). A device checking in on time changes nothing
    the poll shows, so the old signature still answers 204."""
    race_day(event, [("G1", 3)], race=True)
    rows = list(_heartbeats(event))
    assert any(r.exists() for r in rows), "no heartbeat row: the test checks nothing"
    now = timezone.now()
    for r in rows:
        r.update(last_contact=now - datetime.timedelta(seconds=2))
    status, _, sig = _render(coordinator, url)
    assert status == 200
    for r in rows:
        r.update(last_contact=now)
    expected = 200 if url in SWAPS_AT_A_HEARTBEAT else 204
    assert _render(coordinator, url, sig)[0] == expected
