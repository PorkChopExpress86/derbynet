"""Every Undo link a page offers is client input (#44 and #47 retros). After
the real Undo, the same link sent again, or sent with any one of its query
parameters blanked, left out or set to junk, changes nothing stored. #44's
Undo restored a step twice; #47's `restore=1` with an empty `photo` took a
racer's photo away.

Each view that offers an Undo has a scenario in `SCENARIOS`: it sets up the
race and returns the client and the POST whose toast carries the link.
`test_every_undo_has_a_scenario` fails a view that passes `undo_url=` and has
none, so a new Undo is covered the day it exists.
"""

import ast
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from django.apps import apps

from awards.editing import set_winner
from awards.models import Award
from racing.models import PlaylistEntry
from racing.services import heats
from roster.models import Racer
from ui.tests.race import race_setup, run_heat, scheduled
from ui.tests.test_photos import folders, put  # noqa: F401  (a fixture)

pytestmark = pytest.mark.django_db

UI = Path(__file__).resolve().parents[1]
VARIANTS = ("", None, "zzz")  # None leaves the parameter out


def _post(client, url, data=None):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true")


def unassign_photo(event, client, folders):  # noqa: F811
    race_setup(event)
    racer = Racer.objects.filter(event=event).first()
    put(folders / "racers", "a.jpg")
    Racer.objects.filter(pk=racer.pk).update(headshot="a.jpg")
    return f"/photos/head/racer/{racer.pk}/unassign/", {}


def unpass_racer(event, client, folders):  # noqa: F811
    race_setup(event)
    racer = Racer.objects.filter(event=event).first()
    return f"/checkin/racer/{racer.pk}/pass/", {"passed": "0"}


def remove_award_winner(event, client, folders):  # noqa: F811
    wolves = race_setup(event)["Wolves"]
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, group=wolves.group
    )
    set_winner(event, award, Racer.objects.filter(event=event).first())
    return f"/judging/award/{award.pk}/remove/", {}


def discard_results(event, client, folders):  # noqa: F811
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    heats.set_racing(event, False)
    run_heat(event, (2.1, 2.2, 2.3, 2.4))
    return "/dashboard/results/discard/", {"round": wolves.pk, "heat": 1}


def remove_playlist_step(event, client, folders):  # noqa: F811
    wolves = race_setup(event)["Wolves"]
    step = PlaylistEntry.objects.create(
        event=event, seq=1, group=wolves.group, round_number=2, bucket_limit=4
    )
    return f"/playlist/step/{step.pk}/remove/", {}


# "module:view" that offers the Undo -> its scenario.
SCENARIOS = {
    "photos/views.py:unassign": unassign_photo,
    "checkin/views.py:pass_racer": unpass_racer,
    "judging/views.py:remove": remove_award_winner,
    "dashboard/views.py:results_discard": discard_results,
    "playlist/views.py:remove": remove_playlist_step,
}
# Undo links that are not a change, or a variant that is a new request the
# role may make anyway, with the reason.
NOT_COVERED = {
    "views.py:components_demo": "the component gallery's demo toast",
}
SAME_AS_A_NEW_REQUEST = {
    ("photos/views.py:unassign", "restore", ""): "with no restore it is an"
    " assign of that photo, which the role may send",
    ("photos/views.py:unassign", "restore", None): "with no restore it is an"
    " assign of that photo, which the role may send",
    ("checkin/views.py:pass_racer", "passed", ""): "passed=0 is the switch's own"
    " un-pass, which the role may send",
    ("checkin/views.py:pass_racer", "passed", "zzz"): "php_truthy: any text"
    " passes, as the switch's passed=1 does",
}


# Columns a repeat may restamp, with the reason.
RESTAMPED = {
    ("roster.Racer", "checked_in_at"): "racer.pass stamps the time at every"
    " pass, as PHP's does, and the Undo of an un-pass is a pass",
}


def snapshot() -> dict:
    """Every stored row, by model, without the `RESTAMPED` columns."""
    rows = {}
    for model in apps.get_models():
        if model._meta.app_label in ("sessions", "contenttypes"):
            continue
        label = model._meta.label
        columns = [
            f.attname
            for f in model._meta.concrete_fields
            if (label, f.name) not in RESTAMPED
        ]
        rows[label] = sorted(map(repr, model.objects.values_list(*columns)))
    return rows


def undo_link(response) -> str:
    assert response.status_code == 200, response.content[:300]
    found = re.search(r'hx-post="([^"]+)"[^>]*>Undo<', response.content.decode())
    assert found, response.content[:500]
    return found.group(1).replace("&amp;", "&")


def variants(url: str):
    """(parameter, value, url) for each one-parameter change of the link."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    for i, (name, _) in enumerate(query):
        for value in VARIANTS:
            changed = [
                (n, value if j == i else v)
                for j, (n, v) in enumerate(query)
                if j != i or value is not None
            ]
            yield name, value, f"{parts.path}?{urlencode(changed)}"


@pytest.mark.parametrize("key", sorted(SCENARIOS))
def test_an_undo_sent_again_or_altered_changes_nothing(key, event, login, folders):  # noqa: F811
    client = login("RaceCoordinator")
    url, data = SCENARIOS[key](event, client, folders)
    undo = undo_link(_post(client, url, data))
    assert _post(client, undo).status_code == 200
    after_undo = snapshot()

    assert _post(client, undo).status_code < 500
    assert snapshot() == after_undo, f"{undo} sent twice"
    for name, value, changed in variants(undo):
        if (key, name, value) in SAME_AS_A_NEW_REQUEST:
            continue
        assert _post(client, changed).status_code < 500, changed
        assert snapshot() == after_undo, changed


def offering_views() -> set[str]:
    """Each `ui` view that passes an Undo link of its own (`undo_url=` with
    anything but a pass-through of its own `undo_url` or "")."""
    found = set()
    for path in sorted(UI.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            for call in ast.walk(function):
                if not isinstance(call, ast.Call):
                    continue
                for keyword in call.keywords:
                    value = keyword.value
                    if keyword.arg == "undo_url" and not (
                        (isinstance(value, ast.Name) and value.id == "undo_url")
                        or (isinstance(value, ast.Constant) and value.value == "")
                    ):
                        found.add(f"{path.relative_to(UI)}:{function.name}")
    return found


def test_every_undo_has_a_scenario():
    assert offering_views() - set(NOT_COVERED) == set(SCENARIOS)
