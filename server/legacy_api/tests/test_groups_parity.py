"""The group scripts of the shell suite against the PHP recordings, through
`/action.php` (#30).

`roster/tests/test_group_scenarios.py` replays the same recordings through
the services. Here every `class.*`, `rank.*` and `partition.*` request goes
through the Django endpoint, with PHP's ids turned into Django's, and after
each step `query=class.list` must answer what PHP's did, ids left out, as
`recorder.mjs` records it. The partitions, rounds and roster are compared
from the database, since `poll` (#31) and `racer.list` (#29) are not ported.
Racer requests go through the ORM as in `php_replay.Replay`.
"""

import json
from urllib.parse import urlencode

import pytest

from core.models import SiteState
from legacy_api.tests.conftest import log_in, post
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from roster.services import groups
from roster.tests.php_replay import Replay, expected_after, recorded_state
from roster.tests.test_group_scenarios import EXPECTED_FAILURES, _starts_with_reset

pytestmark = pytest.mark.django_db

# The posted fields that hold an id, and the kind of row it names.
ID_FIELDS = {
    "classid": "class",
    "rankid": "rank",
    "partitionid": "partition",
    "div_id": "partition",
}
# `<prefix><n>=<id>` ordering fields, and `<prefix><id>` aggregate fields.
ORDER_FIELDS = {"classid_": "class", "rankid_": "rank", "partitionid_": "partition"}
FLAG_FIELDS = {"constituent_": "class", "rankid_": "rank"}


class HttpReplay(Replay):
    def __init__(self, client):
        self.client = client
        super().__init__()

    def _new_event(self):
        super()._new_event()
        SiteState.objects.filter(pk=1).update(active_event=self.event)

    def _pk(self, kind, php_id) -> str:
        row = self.ids.get(kind, php_id)
        return str(row.pk) if row is not None else "0"

    def _translate(self, action, params):
        out = []
        group_field = dict(params).get("group_field")
        from_field = dict(params).get("from_group_field")
        for key, value in params:
            if key in ID_FIELDS and int(value) >= 0:
                value = self._pk(ID_FIELDS[key], value)
            elif key == "group_id":
                n = int(value)
                if n >= 0:
                    value = self._pk(ID_FIELDS[group_field], n)
                elif group_field == "rankid":
                    value = "-" + self._pk("class", -n)
            elif key == "from_group_id" and from_field in ID_FIELDS:
                value = self._pk(ID_FIELDS[from_field], value)
            elif action == "class.add":
                for prefix, kind in FLAG_FIELDS.items():
                    if key.startswith(prefix):
                        key = prefix + self._pk(kind, key.removeprefix(prefix))
            else:
                for prefix, kind in ORDER_FIELDS.items():
                    if key.startswith(prefix):
                        value = self._pk(kind, value)
            out.append((key, value))
        return out

    def _http(self, values, params):
        action = values["action"]
        reply = json.loads(
            post(self.client, urlencode(self._translate(action, params))).content
        )
        outcome = reply["outcome"]
        if outcome["summary"] != "success":
            self.failures.append((action, outcome["code"]))
        if "classes" in reply:
            assert reply["classes"] == self.class_list(), action
        return reply

    def class_list(self):
        response = self.client.get("/action.php", {"query": "class.list"})
        return json.loads(response.content)["classes"]

    do_class_add = do_class_edit = do_class_delete = do_class_order = _http
    do_rank_add = do_rank_edit = do_rank_delete = do_rank_move = do_rank_order = _http
    do_partition_add = do_partition_edit = do_partition_delete = _http
    do_partition_move = do_partition_order = _http

    def do_partition_apply_rule(self, values, params):
        if values["rule"] != self.settings.group_formation_rule:
            self._http(values, params)
            return
        # D31: the rule in force changes nothing in Django. PHP rebuilds the
        # groups with new ids, which later requests name, so after checking
        # the endpoint changes nothing, the replay rebuilds them as a real
        # rule change does.
        before = self.state()
        self._http(values, params)
        assert self.state() == before
        groups.form_groups_by_rule(self.event, groups.Rule.CUSTOM)
        groups.form_groups_by_rule(self.event, values["rule"])


def _as_recorded(classes):
    """`class.list` as `recorder.mjs` keeps it: no ids, no aggregate keys."""
    return [
        {
            "name": c["name"],
            "count": c["count"],
            "nrounds": c["nrounds"],
            "ntrophies": c["ntrophies"],
            "subgroups": [
                {"name": s["name"], "count": s["count"], "ntrophies": s["ntrophies"]}
                for s in c["subgroups"]
            ],
        }
        for c in classes
    ]


@pytest.mark.parametrize("fixture", EXPECTED_FAILURES)
def test_class_list_answers_what_php_recorded_after_every_step(client, fixture):
    replay = HttpReplay(client)
    log_in(client, -1, role="RaceCoordinator")
    exchanges = read_transcript(GOLDEN_DIR / "http" / "suite" / f"{fixture}.http")
    expected = None
    steps = 0
    for name, exchange in exchanges.items():
        body = json.loads(exchange["body"])
        if expected is None:
            expected = recorded_state(body["state"])
            if not _starts_with_reset(exchanges):
                replay.seed(body["state"])
                assert _as_recorded(replay.class_list()) == expected["classes"]
            continue
        replay.post(exchange["request-body"])
        expected = expected_after(expected, body["changed"])
        assert _as_recorded(replay.class_list()) == expected["classes"], (
            f"{fixture} {name}"
        )
        assert replay.state() == expected, f"{fixture} {name}"
        steps += 1
    assert steps > 0
    assert replay.failures == EXPECTED_FAILURES[fixture]
