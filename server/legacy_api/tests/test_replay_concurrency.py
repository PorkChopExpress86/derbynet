"""Replay polls and message reads at the same moment, on real concurrent
transactions (#34). Run on SQLite and on Postgres."""

import json

from django.utils import timezone

from core.models import SiteState
from kiosks.models import ReplayStatus
from legacy_api.tests.conftest import post
from legacy_api.tests.test_race_control_concurrency import (
    concurrent,
    coordinator,
    in_threads,
    ok,
)
from racing.services import replay

COMMANDS = [f"START Lions_Round1_Heat{n:02d}" for n in range(1, 41)]


@concurrent
def test_two_replay_polls_get_every_command_exactly_once(event):
    event_id = SiteState.objects.get(pk=1).active_event_id
    ReplayStatus.objects.filter(event_id=event_id).update(
        state=ReplayStatus.State.CONNECTED, last_contact=timezone.now()
    )
    for command in COMMANDS:
        assert replay.send(event_id, command)
    clients = [coordinator(), coordinator()]

    def drain(client):
        return lambda: json.loads(
            post(client, "action=replay.message&status=0").content
        )["replay"]

    first, second = in_threads(drain(clients[0]), drain(clients[1]))

    assert sorted(first + second) == sorted(COMMANDS)
    # One poll drained the queue in order, the other found it empty.
    assert sorted([first, second], key=len) == [[], COMMANDS]


@concurrent
def test_a_directed_message_reaches_one_of_two_readers(event):
    clients = [coordinator(), coordinator()]
    ok(clients[0], "action=message.send&message%5Brecipient%5D=AA&message%5Bk%5D=v")

    def read(client):
        return lambda: json.loads(
            post(client, "action=message.retrieve&recipient=AA").content
        )["messages"]

    found = in_threads(read(clients[0]), read(clients[1]))

    assert sorted(found, key=len) == [[], [{"recipient": "AA", "k": "v"}]]
