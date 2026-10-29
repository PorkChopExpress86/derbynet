"""`preferences.upload` and `preferences.php` (#36, S4)."""

import io
import json

import pytest

from accounts.permissions import Perm
from awards.models import Award
from core.models import RaceSettings
from legacy_api.tests.conftest import log_in
from roster.models import Group, GroupConstituent, Partition, Subgroup
from roster.services.groups import delete_group_rows

pytestmark = pytest.mark.django_db

EXAMPLE = """\
# Preferences written 2024-04-27

## Groups
GROUP = Lions & Tigers
  SEGMENT = Lion
  SEGMENT = Lion Pro-Stock
  SEGMENT = Tiger
ENDGROUP
GROUP = Webelos I and II
  SEGMENT = Webelos
ENDGROUP

n-lanes = 4
group-formation-rule = custom
use-subgroups
partition-label = Rank
n-rank-trophies = 3
warn-no-timer

AGGREGATE = All Lions
  CONSTITUENTSUBGROUP = Lion
  CONSTITUENTSUBGROUP = Lion Pro-Stock
ENDAGGREGATE
"""

ONE_GROUP = """\
group-formation-rule = one-group

GROUP = All Racers
  SUBGROUP = Two
    NTROPHIES = 2
  ENDSUBGROUP
  SIMPLE SUBGROUP = Three
  SUBGROUP = Four
    NTROPHIES = 4
    AWARD = Best Four
  ENDSUBGROUP
ENDGROUP

use-subgroups
n-den-trophies = 0
"""


def upload(client, text):
    response = client.post(
        "/action.php",
        {"action": "preferences.upload", "prefs": io.BytesIO(text.encode())},
    )
    return json.loads(response.content)


@pytest.fixture
def coordinator(client, event):
    log_in(client, Perm.SET_UP, role="RaceCoordinator")
    return event


def names(model, event, **where):
    return list(
        model.objects.filter(event=event, **where).values_list("name", flat=True)
    )


def test_a_login_without_set_up_is_refused(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    reply = upload(client, "n-lanes = 4")

    assert reply["outcome"]["code"] == "notauthorized"


def test_the_example_file_builds_groups_subgroups_aggregates_and_settings(
    client, coordinator
):
    Group.objects.filter(event=coordinator).delete()

    reply = upload(client, EXAMPLE)

    assert reply["outcome"]["summary"] == "success", reply
    assert "details" in reply
    assert names(Group, coordinator, durable=False) == [
        "Lions & Tigers",
        "Webelos I and II",
    ]
    assert names(Subgroup, coordinator, group__name="Lions & Tigers") == [
        "Lion",
        "Lion Pro-Stock",
        "Tiger",
    ]
    assert names(Partition, coordinator).count("Lion") == 1
    settings = RaceSettings.objects.get(event=coordinator)
    assert (settings.lane_count, settings.group_formation_rule) == (4, "custom")
    assert settings.use_subgroups and settings.partition_label == "Rank"
    aggregate = Group.objects.get(event=coordinator, name="All Lions")
    assert aggregate.durable
    assert GroupConstituent.objects.filter(aggregate=aggregate).count() == 1


def test_subgroup_trophies_awards_and_simple_subgroups(client, coordinator):
    Group.objects.filter(event=coordinator).delete()

    reply = upload(client, ONE_GROUP)

    assert reply["outcome"]["summary"] == "success", reply
    two, three, four = (
        Subgroup.objects.get(event=coordinator, name=n)
        for n in ("Two", "Three", "Four")
    )
    assert (two.n_trophies, three.n_trophies, four.n_trophies) == (2, None, 4)
    award = Award.objects.get(event=coordinator)
    assert (award.name, award.subgroup_id, award.group_id) == (
        "Best Four",
        four.pk,
        None,
    )


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("ENDGROUP", "endgroup-no-group"),
        ("SUBGROUP = X", "subgroup-needs-a-group"),
        ("NTROPHIES = 2", "ntrophies"),
        ("ENDAGGREGATE", "endaggregate-no-aggregate"),
        ("GROUP = A\nGROUP = B", "group-within-group"),
        ("no-such-setting = 1", "prefs-syntax"),
        ("n-lanes", "prefs-syntax"),
        ("CONSTITUENT GROUP = A", "constituentgroup"),
    ],
)
def test_a_bad_line_fails_with_phps_code(client, coordinator, text, code):
    assert upload(client, text)["outcome"]["code"] == code


def test_the_failure_names_the_line(client, coordinator):
    reply = upload(client, "n-lanes = 4\n\nbogus = 1")

    assert reply["outcome"]["description"] == (
        "Preferences file syntax, line 3: bogus = 1 (1)"
    )


def test_preferences_php_needs_set_up_and_leaves_out_the_ballot_password(
    client, coordinator
):
    RaceSettings.objects.filter(event=coordinator).update(
        ballot_password="hunter2", lane_count=6
    )

    response = client.get("/preferences.php")

    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    assert "n-lanes = 6" in text and "hunter2" not in text and "ballot" not in text


def test_preferences_php_refuses_anonymous_and_lesser_roles(client, event):
    log_in(client, Perm.CONTROL_RACE, role="RaceCrew")

    assert client.get("/preferences.php").status_code == 302


def test_a_dumped_file_loads_back(client, coordinator):
    Group.objects.filter(event=coordinator).delete()
    upload(
        client,
        EXAMPLE
        + "\nGROUP = Extra\n  NTROPHIES = 5\n  SEGMENT = Extra Den\n"
        + "  AWARD = Spirit\nENDGROUP\n",
    )
    dumped = client.get("/preferences.php").content.decode()
    for group in Group.objects.filter(event=coordinator).order_by("-id"):
        if Group.objects.filter(pk=group.pk).exists():
            delete_group_rows(group)
    Partition.objects.filter(event=coordinator).delete()

    reply = upload(client, dumped)

    assert reply["outcome"]["summary"] == "success", (reply, dumped)
    assert Group.objects.get(event=coordinator, name="Extra").n_trophies == 5
    assert names(Award, coordinator) == ["Spirit"]
    assert Group.objects.get(event=coordinator, name="All Lions").durable
