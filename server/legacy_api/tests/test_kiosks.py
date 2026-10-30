"""The kiosk endpoints (#34): `poll.kiosk`, `poll.kiosk.all`, `kiosk.assign`
and `kiosk.reload`, with PHP's wire contract and the fixes of C18, C19, C20
and S27."""

import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.utils import timezone

from accounts.permissions import Perm
from kiosks.models import Kiosk
from legacy_api.tests.conftest import log_in, post

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def act(client, action_name, **fields):
    body = urlencode({"action": action_name, **fields})
    return json.loads(post(client, body).content)


def poll(client, address=None):
    params = {"query": "poll.kiosk"}
    if address is not None:
        params["address"] = address
    return json.loads(client.get("/action.php", params).content)["kiosk-setting"]


def dashboard(client):
    return json.loads(client.get("/action.php", {"query": "poll.kiosk.all"}).content)


def test_a_new_kiosk_is_told_to_identify_itself(client, event):
    assert poll(client, "K1") == {
        "page": "kiosks/identify.kiosk",
        "parameters": "{}",
        "reload": False,
    }
    assert poll(client, "K1") == {
        "page": "kiosks/identify.kiosk",
        "name": "",
        "reload": False,
        "parameters": "{}",
    }


def test_a_poll_without_an_address_gets_no_setting(client, event):
    assert poll(client) is None


def test_a_reload_shows_once_and_answers_success(coordinator):
    poll(coordinator, "K1")
    act(coordinator, "kiosk.assign", address="K1", page="kiosks/welcome.kiosk")

    reply = act(coordinator, "kiosk.reload", address="K1")

    # C18: PHP never sets an outcome, so its reply stays in-progress.
    assert reply["outcome"]["summary"] == "success"
    assert poll(coordinator, "K1")["reload"] is True
    assert poll(coordinator, "K1")["reload"] is False


def test_parameters_come_back_as_the_text_that_was_sent(coordinator):
    poll(coordinator, "K1")
    act(
        coordinator,
        "kiosk.assign",
        address="K1",
        page="kiosks/standings.kiosk",
        params="title=Final Standings",
    )

    setting = poll(coordinator, "K1")

    assert setting["page"] == "kiosks/standings.kiosk"
    assert setting["parameters"] == "title=Final Standings"


def test_parameters_alone_keep_the_page(coordinator):
    poll(coordinator, "K1")
    act(coordinator, "kiosk.assign", address="K1", page="kiosks/flag.kiosk")
    act(coordinator, "kiosk.assign", address="K1", params='{"a":1}')

    setting = poll(coordinator, "K1")

    assert (setting["page"], setting["parameters"]) == ("kiosks/flag.kiosk", '{"a":1}')


def test_the_dashboard_shows_each_kiosks_own_parameters(coordinator):
    """C20: PHP reads a column key that does not exist, so it shows null."""
    for address in ("K1", "K2"):
        poll(coordinator, address)
    act(coordinator, "kiosk.assign", address="K2", page="kiosks/x.kiosk", params="p")

    kiosks = {k["address"]: k for k in dashboard(coordinator)["kiosks"]}

    assert kiosks["K1"]["parameters"] == "{}"
    assert kiosks["K2"]["parameters"] == "p"


def test_the_dashboard_lists_unnamed_kiosks_then_main_then_by_name(coordinator):
    for address, name in (("D", "Aux2"), ("C", "Main"), ("B", None), ("A", "Aux1")):
        poll(coordinator, address)
        if name:
            act(coordinator, "kiosk.assign", address=address, name=name)

    kiosks = dashboard(coordinator)["kiosks"]

    assert [k["address"] for k in kiosks] == ["B", "C", "A", "D"]


def test_an_anonymous_caller_cannot_list_the_kiosks(client, event):
    """S27: PHP lists every kiosk address to anyone."""
    poll(client, "K1")

    reply = dashboard(client)

    assert reply["outcome"]["code"] == "notauthorized"
    assert "kiosks" not in reply


def test_the_award_presenter_can_list_the_kiosks(client, event):
    log_in(client, Perm.PRESENT_AWARDS)
    poll(client, "K1")

    assert [k["address"] for k in dashboard(client)["kiosks"]] == ["K1"]


def test_a_kiosk_silent_for_an_hour_is_dropped(coordinator, event):
    poll(coordinator, "old")
    poll(coordinator, "quiet")
    now = timezone.now()
    Kiosk.objects.filter(address="old").update(
        last_contact=now - timedelta(seconds=3601)
    )
    Kiosk.objects.filter(address="quiet").update(
        last_contact=now - timedelta(seconds=120)
    )
    Kiosk.objects.create(event=event, address="never")

    assert dashboard(coordinator)["kiosks"] == []
    assert set(Kiosk.objects.values_list("address", flat=True)) == {"quiet", "never"}


def test_the_dashboard_offers_the_built_in_pages(coordinator):
    pages = dashboard(coordinator)["kiosk-pages"]

    assert pages[0] == {
        "brief": "award-presentations",
        "full": "kiosks/award-presentations.kiosk",
    }
    assert len(pages) == 15
