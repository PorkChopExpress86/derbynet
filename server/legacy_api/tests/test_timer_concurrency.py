"""Timer messages at the same moment, on real concurrent transactions
(#32). Run on SQLite and on Postgres."""

from django.test import Client

from legacy_api.tests.conftest import post
from legacy_api.tests.test_race_control_concurrency import (
    concurrent,
    coordinator,
    in_threads,
    ok,
)

TIMER_LOGIN = "action=role.login&name=Timer&password="


def timer() -> Client:
    client = Client(headers={"User-Agent": "DerbyNet-Timer/concurrency"})
    ok(client, TIMER_LOGIN)
    return client


@concurrent
def test_one_remote_start_reaches_exactly_one_of_20_heartbeats(event):
    jar = timer()
    post(jar, "action=timer-message&message=HELLO&remote-start=YES")
    ok(coordinator(), "action=timer.remote-start")
    clients = [timer() for _ in range(20)]

    def heartbeat(client):
        return lambda: post(
            client, "action=timer-message&message=HEARTBEAT&remote-start=YES"
        ).content.decode()

    bodies = in_threads(*(heartbeat(c) for c in clients))
    assert all("<success/>" in body for body in bodies), bodies
    assert sum(body.count("<remote-start/>") for body in bodies) == 1
