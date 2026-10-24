import json
import logging
import xml.etree.ElementTree as ET

import pytest
from django.test import override_settings

from accounts.permissions import Access, Perm
from core.invariants import check_invariants
from core.models import SiteState
from core.tests.factories import EventFactory
from legacy_api.registry import SessionWriteError
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane, Round
from racing.tests.factories import RoundFactory
from roster.models import Racer
from roster.tests.factories import RacerFactory

pytestmark = pytest.mark.django_db

NOT_A_PAGE = (
    b"<!DOCTYPE html><html><head><title>Not a Page</title></head>"
    b"<body><h1>This is not a page.</h1></body></html>"
)


def outcome(response):
    return json.loads(response.content)["outcome"]


def test_a_request_with_no_fields_is_not_a_page(client, event):
    for response in (client.get("/action.php"), post(client, "")):
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=UTF-8"
        assert response.content == NOT_A_PAGE


def test_a_post_with_fields_is_an_action_named_by_the_body(client, event):
    response = post(client, "action=no.such", path="/action.php?query=poll")

    assert outcome(response)["description"] == "Unrecognized action: no.such"


def test_a_get_is_a_query_named_by_the_query_string(client, event):
    response = client.get("/action.php", {"query": "no.such"})

    assert outcome(response) == {
        "summary": "failure",
        "code": "unrecognized",
        "description": "Unrecognized query: no.such",
    }


def test_an_array_name_is_unrecognized_where_php_answers_500(client, event):
    response = client.get("/action.php?query[]=poll")

    assert response.status_code == 200
    assert outcome(response)["description"] == "Unrecognized query: "


def test_every_response_forbids_caching(client, event):
    for response in (client.get("/action.php"), client.get("/action.php?query=x")):
        assert response["Cache-Control"] == "no-store, no-cache, must-revalidate"


def test_with_no_event_open_a_name_redirects_to_setup(client, db):
    for response in (
        client.get("/action.php?query=poll.coordinator"),
        post(client, "action=role.login&name=x&password=y"),
        client.get("/action.php?query=no.such"),
    ):
        assert response.status_code == 302
        assert response["Location"] == "setup.php"
        assert response["Content-Type"] == "application/json; charset=utf-8"
        assert response.content == b""


def test_with_no_event_open_a_nodata_name_runs(client, db, endpoint):
    endpoint("query", "probe.nodata", lambda ctx: ctx.put("event", ctx.event))

    assert client.get("/action.php?query=probe.nodata").content == (
        b'{\n    "event": null\n}\n'
    )


def test_a_query_that_writes_nothing_answers_an_empty_object(client, event, endpoint):
    endpoint("query", "probe.empty")

    assert client.get("/action.php?query=probe.empty").content == b"{}\n"


def test_an_action_starts_in_progress_and_the_last_outcome_wins(
    client, event, endpoint
):
    def handler(ctx):
        assert ctx.out["outcome"]["code"] == "in-progress"
        ctx.failure("first", "replaced")
        ctx.success()
        ctx.put("n", "007")

    endpoint("action", "probe.outcome", handler)

    body = json.loads(post(client, "action=probe.outcome&password=pw").content)
    assert body == {
        "action": {"action": "probe.outcome", "password": "..."},
        "outcome": {"summary": "success", "code": "success", "description": ""},
        "n": 7,
    }


def test_a_handler_reads_the_parsed_php_form(client, event, endpoint):
    seen = {}
    endpoint("action", "probe.args", lambda ctx: seen.update(ctx.args))

    post(client, "action=probe.args&password=pw&message[recipient]=AA&unhealthy")

    assert seen == {
        "action": "probe.args",
        "password": "pw",
        "message": {"recipient": "AA"},
        "unhealthy": "",
    }


def test_a_json_handler_that_raises_answers_server_error(
    client, event, endpoint, caplog
):
    def handler(ctx):
        ctx.put("partial", 1)
        raise RuntimeError("boom")

    endpoint("action", "probe.raises", handler)

    with caplog.at_level(logging.ERROR, logger="legacy_api.views"):
        response = post(client, "action=probe.raises&password=secret")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(response.content) == {
        "action": {"action": "probe.raises", "password": "..."},
        "outcome": {
            "summary": "failure",
            "code": "server-error",
            "description": "Unexpected server error",
        },
    }
    assert "probe.raises raised" in caplog.text
    assert "secret" not in caplog.text


def test_an_xml_handler_that_raises_mid_document_answers_well_formed_xml(
    client, event, endpoint
):
    def handler(ctx):
        ctx.xml.start_response()
        ctx.xml.write("<half")
        raise RuntimeError("boom")

    endpoint("action", "snapshot.raises", handler)

    response = post(client, "action=snapshot.raises&q='")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/xml; charset=utf-8"
    assert response.content == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<action-response action="snapshot.raises" q="&#039;">'
        b'<failure code="server-error">Unexpected server error</failure>'
        b"</action-response>\n"
    )
    root = ET.fromstring(response.content)
    assert root.tag == "action-response"
    assert root.find("failure").get("code") == "server-error"


def test_a_value_php_cannot_encode_answers_server_error(client, event, endpoint):
    endpoint("query", "probe.inf", lambda ctx: ctx.put("x", float("inf")))

    response = client.get("/action.php?query=probe.inf")

    assert outcome(response)["code"] == "server-error"


def test_a_permission_refusal_never_runs_the_handler(client, event, endpoint):
    ran = []
    endpoint(
        "action", "probe.race", lambda ctx: ran.append(1), permission=Perm.CONTROL_RACE
    )

    response = post(client, "action=probe.race")

    assert ran == []
    assert outcome(response) == {
        "summary": "failure",
        "code": "notauthorized",
        "description": "Not authorized -- please see race coordinator.",
    }


@pytest.mark.parametrize(
    "granted", [Perm.CONTROL_RACE, Perm.CONTROL_RACE | Perm.SET_UP, -1]
)
def test_a_session_holding_the_bit_runs_the_handler(client, event, endpoint, granted):
    endpoint(
        "action", "probe.race", lambda ctx: ctx.success(), permission=Perm.CONTROL_RACE
    )
    log_in(client, granted)

    assert outcome(post(client, "action=probe.race"))["code"] == "success"


def test_a_session_without_the_bit_is_refused(client, event, endpoint):
    endpoint("action", "probe.race", permission=Perm.CONTROL_RACE)
    log_in(client, Perm.SET_UP | Perm.TIMER_MESSAGE)

    assert outcome(post(client, "action=probe.race"))["code"] == "notauthorized"


def test_any_one_bit_of_a_requirement_is_enough(client, event, endpoint):
    endpoint(
        "query",
        "probe.pass",
        lambda ctx: ctx.success(),
        permission=Perm.CHECK_IN_RACERS | Perm.PHOTO_UPLOAD,
    )
    log_in(client, Perm.PHOTO_UPLOAD)

    assert outcome(client.get("/action.php?query=probe.pass"))["code"] == "success"


def test_the_permission_code_answers_with_its_own_text(client, event, endpoint):
    endpoint("action", "probe.class", permission=Perm.SET_UP, failure_code="permission")

    assert outcome(post(client, "action=probe.class")) == {
        "summary": "failure",
        "code": "permission",
        "description": "Not authorized",
    }


def test_an_xml_refusal_is_not_authorized_body(client, event, endpoint):
    endpoint("action", "snapshot.probe", permission=Perm.SET_UP)

    response = post(client, "action=snapshot.probe&message=HELLO")

    assert response.content == (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<action-response action="snapshot.probe" message="HELLO">'
        b'<failure code="notauthorized">Not authorized -- please see race coordinator'
        b"</failure></action-response>\n"
    )


def test_logged_in_means_a_role_name_in_the_session(client, event, endpoint):
    endpoint(
        "query", "probe.member", lambda ctx: ctx.success(), permission=Access.LOGGED_IN
    )
    url = "/action.php?query=probe.member"

    assert outcome(client.get(url))["code"] == "notauthorized"
    log_in(client, 0, role="RaceCrew")
    assert outcome(client.get(url))["code"] == "success"


def test_a_handler_without_session_write_cannot_write_the_session(
    client, event, endpoint, caplog
):
    def handler(ctx):
        ctx.session["sneaky"] = 1

    endpoint("action", "probe.writes", handler)

    with caplog.at_level(logging.ERROR, logger="legacy_api.views"):
        response = post(client, "action=probe.writes")

    assert outcome(response)["code"] == "server-error"
    assert SessionWriteError.__name__ in caplog.text
    assert "sneaky" not in client.session


def test_the_read_only_session_raises_directly(client, event, endpoint):
    raised = []

    def handler(ctx):
        with pytest.raises(SessionWriteError):
            ctx.session["sneaky"] = 1
        with pytest.raises(SessionWriteError):
            ctx.session.pop("role", None)
        raised.append(dict(ctx.session))
        ctx.success()

    endpoint("action", "probe.writes", handler)
    log_in(client, Perm.SET_UP)

    assert outcome(post(client, "action=probe.writes"))["code"] == "success"
    assert raised == [{"role": "Tester", "permissions": int(Perm.SET_UP)}]


@override_settings(LEGACY_SESSION_WRITES_RAISE=False)
def test_in_production_a_session_write_is_dropped_with_a_warning(
    client, event, endpoint, caplog
):
    def handler(ctx):
        ctx.session["sneaky"] = 1
        ctx.success()

    endpoint("action", "probe.writes", handler)

    with caplog.at_level(logging.WARNING, logger="legacy_api.registry"):
        response = post(client, "action=probe.writes")

    assert outcome(response)["code"] == "success"
    assert "probe.writes writes the session" in caplog.text
    assert "sneaky" not in client.session


def test_a_handler_with_session_write_writes_the_session(client, event, endpoint):
    def handler(ctx):
        ctx.session["sneaky"] = 1
        ctx.success()

    endpoint("action", "probe.writes", handler, session_write=True)

    assert outcome(post(client, "action=probe.writes"))["code"] == "success"
    assert client.session["sneaky"] == 1


def stage_racer(ctx):
    """A timer-like handler that stages a posted racer in a posted round."""
    racer = ctx.resolve(Racer, ctx.args.get("racerid"))
    round_ = ctx.resolve(Round, ctx.args.get("roundid"))
    if racer is None or round_ is None:
        ctx.failure("notfound", "No such racer or round")
        return
    HeatLane.objects.create(event=ctx.event, round=round_, racer=racer, heat=1, lane=1)
    ctx.success()


def test_a_timer_posting_an_id_from_the_old_event_after_a_switch_writes_nothing(
    client, endpoint
):
    old, new = EventFactory(), EventFactory()
    old_racer = RacerFactory(group__event=old)
    new_round = RoundFactory(group__event=new)
    endpoint("action", "probe.stage", stage_racer, permission=Perm.TIMER_MESSAGE)
    log_in(client, Perm.TIMER_MESSAGE, role="Timer")
    SiteState.objects.filter(pk=1).update(active_event=new)

    response = post(
        client, f"action=probe.stage&racerid={old_racer.pk}&roundid={new_round.pk}"
    )

    assert outcome(response)["code"] == "notfound"
    assert not HeatLane.objects.exists()
    assert "cross-event" not in check_invariants(new)
    assert "cross-event" not in check_invariants(old)


def test_a_timer_posting_an_id_of_the_active_event_is_staged(client, endpoint):
    EventFactory()
    new = EventFactory()
    racer = RacerFactory(group__event=new)
    new_round = RoundFactory(group=racer.group)
    endpoint("action", "probe.stage", stage_racer, permission=Perm.TIMER_MESSAGE)
    log_in(client, Perm.TIMER_MESSAGE, role="Timer")
    SiteState.objects.filter(pk=1).update(active_event=new)

    response = post(
        client, f"action=probe.stage&racerid={racer.pk}&roundid={new_round.pk}"
    )

    assert outcome(response)["code"] == "success"
    assert HeatLane.objects.get().event == new
    assert "cross-event" not in check_invariants(new)
