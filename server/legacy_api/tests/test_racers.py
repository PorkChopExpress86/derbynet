"""The racer, check-in and import endpoints (#29), through `/action.php`."""

import json
import re

import pytest

from accounts.permissions import Perm
from awards.services import partition_list, set_partition_list
from core.models import ActionLog, RaceSettings
from legacy_api.tests.conftest import log_in, post
from racing.models import HeatLane, RosterEntry, Round
from racing.tests.factories import HeatLaneFactory
from roster.models import Partition, Racer
from roster.services import groups

pytestmark = pytest.mark.django_db


def act(client, body):
    return json.loads(post(client, body).content)


def get(client, query):
    return json.loads(client.get(f"/action.php?query={query}").content)


@pytest.fixture
def coordinator(client, event):
    log_in(client, -1, role="RaceCoordinator")
    return client


def role_login(client, name, password):
    body = act(client, f"action=role.login&name={name}&password={password}")
    assert body["outcome"]["code"] == "success"
    return client


def imported(client, body):
    reply = act(client, f"action=racer.import&{body}")
    assert reply["outcome"]["code"] == "success", reply
    return Racer.objects.get(pk=reply["racerid"])


# ----- racer.import -----


def test_import_joins_a_partition_named_with_other_case(coordinator, event):
    """D34: PHP makes a second partition `LIONS` beside `Lions`."""
    lions = imported(coordinator, "firstname=A&lastname=One&partition=Lions").partition

    racer = imported(coordinator, "firstname=B&lastname=Two&partition=LIONS")

    assert racer.partition == lions
    assert list(
        Partition.objects.filter(event=event).values_list("name", flat=True)
    ) == ["Lions"]


@pytest.mark.parametrize(
    ("first_last", "first", "last"),
    [
        ("Blake Burling", "Blake", "Burling"),
        (" Bruce  Boissonneault ", "Bruce", "Boissonneault"),
        ("Craney, Carey", "Carey", "Craney"),
        ("Cybulski,Carroll", "Carroll", "Cybulski"),
        ("Cher", "", "Cher"),
    ],
)
def test_import_splits_first_last(coordinator, first_last, first, last):
    racer = imported(coordinator, f"first-last={first_last}&partition=Lions")

    assert (racer.first_name, racer.last_name) == (first, last)


def test_import_with_no_partition_uses_default_and_numbers_the_car(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B")

    assert racer.partition.name == "Default"
    assert racer.car_number == 101


def test_import_requires_both_names(coordinator, event):
    reply = act(coordinator, "action=racer.import&firstname=A")

    assert reply["outcome"]["code"] == "missingrequired"
    assert reply["outcome"]["description"] == "Missing required field lastname."
    assert not Racer.objects.filter(event=event).exists()


def test_import_warns_of_a_duplicate_car_number(coordinator):
    imported(coordinator, "firstname=A&lastname=B&carnumber=101")

    reply = act(coordinator, "action=racer.import&firstname=C&lastname=D&carnumber=101")

    assert reply["warnings"] == ["Duplicate car number 101"]
    assert reply["outcome"]["code"] == "success"


def test_import_logs_the_synthetic_create(coordinator, event):
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions&carnumber=7")

    logged = ActionLog.objects.get(event=event, request__action="racer.*create")
    assert logged.request == {
        "action": "racer.*create",
        "racerid": racer.pk,
        "firstname": "A",
        "lastname": "B",
        "carnumber": "7",
        "carname": "",
        "partitionid": racer.partition_id,
        "rankid": racer.subgroup_id,
        "classid": racer.group_id,
    }


def test_import_needs_set_up(client, event):
    log_in(client, Perm.REGISTER_NEW_RACER)

    assert (
        act(client, "action=racer.import&firstname=A&lastname=B")["outcome"]["code"]
        == "notauthorized"
    )


# ----- racer.add -----


def test_add_with_no_partition_lands_in_default(coordinator, event):
    """C15: PHP reads an undefined `$partitionid`."""
    groups.find_or_create_partition(event.id, "Lions")

    for partitionid in ("", "&partitionid=-1", "&partitionid=0", "&partitionid=9999"):
        reply = act(
            coordinator, f"action=racer.add&firstname=A&lastname=B{partitionid}"
        )
        assert reply["outcome"]["code"] == "success"
        assert reply["new-row"]["partition"] == "Default"


def test_add_returns_the_check_in_row_and_next_numbers(coordinator, event):
    lions = groups.find_or_create_partition(event.id, "Lions")

    reply = act(
        coordinator,
        f"action=racer.add&firstname=Fred&lastname=Flintstone&carno=&carname=Rock"
        f"&partitionid={lions.pk}&note_from=Bedrock",
    )

    racer = Racer.objects.get(event=event, last_name="Flintstone")
    row = reply["new-row"]
    assert row["racerid"] == racer.pk
    assert (row["carnumber"], row["note"], row["carname"]) == (101, "Bedrock", "Rock")
    assert row["headshot"] == "img/photo-headshot.png"
    assert row["carphoto"] == "img/photo-car.png"
    assert (row["scheduled"], row["passed"], row["xbs"]) == (False, False, 0)
    assert row["rankseq"] == 1
    assert reply["car-numbers"] == [{"partitionid": lions.pk, "next_carnumber": 102}]
    assert list(reply) == ["action", "outcome", "new-row", "car-numbers"]


def test_add_needs_register_new_racer(client, event):
    log_in(client, Perm.CHECK_IN_RACERS)

    reply = act(client, "action=racer.add&firstname=A&lastname=B")

    assert reply["outcome"]["code"] == "notauthorized"


# ----- racer.edit -----


def test_edit_to_another_partition_moves_the_round_1_entry(coordinator, event):
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions")
    tigers = groups.find_or_create_partition(event.id, "Tigers")

    reply = act(
        coordinator, f"action=racer.edit&racer={racer.pk}&partitionid={tigers.pk}"
    )

    assert reply["outcome"]["code"] == "success"
    assert reply["partition-sortorder"] == tigers.sort_order
    racer.refresh_from_db()
    assert racer.group.name == "Tigers"
    rounds = RosterEntry.objects.filter(racer=racer).values_list("round", flat=True)
    assert list(rounds) == [Round.objects.get(group=racer.group, number=1).pk]


def test_edit_refuses_an_unknown_partition_or_subgroup(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions")

    bad_partition = act(
        coordinator, f"action=racer.edit&racer={racer.pk}&partitionid=-1"
    )
    bad_rank = act(coordinator, f"action=racer.edit&racer={racer.pk}&rankid=-1")

    assert bad_partition["outcome"]["code"] == "nopartition"
    assert bad_partition["partition-sortorder"] is False
    assert bad_rank["outcome"]["code"] == "noclass"


def test_edit_changes_the_fields_and_warns_of_a_duplicate(coordinator):
    imported(coordinator, "firstname=A&lastname=B&carnumber=101")
    racer = imported(coordinator, "firstname=C&lastname=D&carnumber=102")

    reply = act(
        coordinator,
        f"action=racer.edit&racer={racer.pk}&firstname=Carroll&lastname=Cybulski"
        "&carno=101&carname=Vroom&exclude=1",
    )

    # PHP interpolates an unset key, so its warning has no number.
    assert reply["warning"] == ["Duplicate car number "]
    racer.refresh_from_db()
    assert (racer.first_name, racer.car_number, racer.car_name, racer.exclude) == (
        "Carroll",
        101,
        "Vroom",
        True,
    )


def _left_behind(coordinator, event):
    """A racer of partition `Tigers` left in group `Lions`, as a filtered
    `partition.move` leaves one, and Lions' award list narrowed to Tigers."""
    lions = imported(coordinator, "firstname=A&lastname=B&partition=Lions")
    tiger = imported(coordinator, "firstname=C&lastname=D&partition=Tigers")
    Racer.objects.filter(pk=tiger.pk).update(group=lions.group, subgroup=lions.subgroup)
    set_partition_list(event, [tiger.partition], group=lions.group)
    assert partition_list(event, group=lions.group) == [tiger.partition]
    return lions, tiger


def test_edit_out_of_a_group_reconciles_its_award_list(coordinator, event):
    """D38 (#106 review): without the reconcile the stale row empties
    Lions' list."""
    lions, tiger = _left_behind(coordinator, event)

    act(
        coordinator,
        f"action=racer.edit&racer={tiger.pk}&partitionid={tiger.partition_id}",
    )

    assert partition_list(event, group=lions.group) == [lions.partition]


def test_delete_out_of_a_group_reconciles_its_award_list(coordinator, event):
    lions, tiger = _left_behind(coordinator, event)

    act(coordinator, f"action=racer.delete&racer={tiger.pk}")

    assert partition_list(event, group=lions.group) == [lions.partition]


# ----- racer.delete -----


def test_delete_removes_the_racer_and_logs_it(coordinator, event):
    racer = imported(coordinator, "firstname=Soon&lastname=ToGo&partition=Lions")

    reply = act(coordinator, f"action=racer.delete&racer={racer.pk}")

    assert reply["outcome"]["code"] == "success"
    assert reply["reload"] is True
    assert not Racer.objects.filter(pk=racer.pk).exists()
    assert ActionLog.objects.filter(
        event=event, request={"action": "racer.*delete", "racerid": str(racer.pk)}
    ).exists()


def test_delete_refuses_a_scheduled_racer(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions")
    HeatLaneFactory(round=Round.objects.get(group=racer.group), racer=racer)

    reply = act(coordinator, f"action=racer.delete&racer={racer.pk}")

    assert reply["outcome"]["code"] == "scheduled"
    assert "reload" not in reply


# ----- racer.bulk -----


def test_bulk_without_edit_racer_answers_json(client, event):
    """C21: PHP writes XML into the JSON response."""
    log_in(client, Perm.CHECK_IN_RACERS)

    response = post(client, "action=racer.bulk&who=all&what=checkin")

    assert response["Content-Type"].startswith("application/json")
    reply = json.loads(response.content)
    assert reply["outcome"]["code"] == "notauthorized"
    # PHP writes reload after its refusal too.
    assert reply["reload"] is True


def test_bulk_un_check_in_without_revert_answers_json(client, event):
    log_in(client, Perm.EDIT_RACER)
    groups.find_or_create_partition(event.id, "Lions")
    log_in(client, -1)
    imported(client, "firstname=A&lastname=B&partition=Lions")
    log_in(client, Perm.EDIT_RACER)

    reply = act(client, "action=racer.bulk&who=all&what=checkin&value=0")

    assert reply["outcome"]["code"] == "notauthorized"
    assert reply["reload"] is True


def test_bulk_checks_in_one_group(coordinator, event):
    lion = imported(coordinator, "firstname=A&lastname=B&partition=Lions")
    tiger = imported(coordinator, "firstname=C&lastname=D&partition=Tigers")

    reply = act(coordinator, f"action=racer.bulk&who=c{lion.group_id}&what=checkin")

    assert (reply["outcome"]["code"], reply["reload"]) == ("success", True)
    assert Racer.objects.get(pk=lion.pk).passed_inspection
    assert not Racer.objects.get(pk=tiger.pk).passed_inspection


def test_bulk_renumbers_from_start_in_id_order(coordinator):
    first = imported(
        coordinator, "firstname=A&lastname=B&partition=Webelos&carnumber=579"
    )
    second = imported(
        coordinator, "firstname=C&lastname=D&partition=Webelos&carnumber=504"
    )

    act(coordinator, f"action=racer.bulk&what=number&who=c{first.group_id}&start=501")

    assert [Racer.objects.get(pk=r.pk).car_number for r in (first, second)] == [
        501,
        502,
    ]


def test_bulk_auto_renumbers_by_partition_order(coordinator, event):
    wolf = imported(coordinator, "firstname=A&lastname=B&partition=Wolves")
    lion = imported(coordinator, "firstname=C&lastname=D&partition=Lions")
    groups.order_partitions(event, [lion.partition, wolf.partition])

    act(coordinator, "action=racer.bulk&what=number&who=all&renumber=1&auto=1")

    assert Racer.objects.get(pk=lion.pk).car_number == 101
    assert Racer.objects.get(pk=wolf.pk).car_number == 201


def test_bulk_eligibility(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B")

    act(coordinator, "action=racer.bulk&who=all&what=eligibility&value=0")

    assert Racer.objects.get(pk=racer.pk).exclude


def test_bulk_unknown_what_and_who(coordinator):
    assert (
        act(coordinator, "action=racer.bulk&who=all&what=x")["outcome"]["code"]
        == "what"
    )
    assert (
        act(coordinator, "action=racer.bulk&who=x&what=checkin")["outcome"]["code"]
        == "who"
    )


# ----- racer.pass -----


def test_anonymous_cannot_check_a_racer_in(client, coordinator, event):
    """S23: PHP checks anyone's racer in and makes walk-ins for them."""
    racer = imported(coordinator, "firstname=A&lastname=B&carnumber=101")
    client.post("/action.php", {"action": "role.login", "name": ""})

    by_id = act(client, f"action=racer.pass&racer={racer.pk}&value=1")
    walk_in = act(client, "action=racer.pass&barcode=PWDwi305")

    assert by_id["outcome"]["code"] == "notauthorized"
    assert walk_in["outcome"]["code"] == "notauthorized"
    assert not Racer.objects.get(pk=racer.pk).passed_inspection
    assert not Racer.objects.filter(event=event, car_number=305).exists()


def test_the_photo_role_checks_in_by_barcode_as_the_photo_stand_posts(
    client, coordinator, event
):
    """`maybe_check_in_racer` in `extras/scripts/lib/photo-functions.sh`: a
    multipart post, grepped for success, names read with sed."""
    imported(
        coordinator, "firstname=Adolfo&lastname=Asher&partition=Lions&carnumber=101"
    )
    role_login(client, "Photo", "flashbulb")

    response = client.post(
        "/action.php", {"action": "racer.pass", "barcode": "PWD101", "value": "1"}
    )

    text = response.content.decode()
    assert "success" in text
    # grep firstname | sed -e 's/.*: "\(.*\)".*/\1/'
    for key, name in (("firstname", "Adolfo"), ("lastname", "Asher")):
        (line,) = [line for line in text.splitlines() if key in line]
        assert re.sub(r'.*: "(.*)".*', r"\1", line) == name
    assert json.loads(text)["details"] == {
        "class": "Lions",
        "firstname": "Adolfo",
        "lastname": "Asher",
    }
    racer = Racer.objects.get(event=event, car_number=101)
    assert racer.passed_inspection and racer.checked_in_at is not None


def test_the_photo_role_cannot_revert_a_check_in(client, coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B")
    role_login(client, "Photo", "flashbulb")

    reply = act(client, f"action=racer.pass&racer={racer.pk}&value=0")

    assert reply["outcome"]["code"] == "notauthorized"


def test_pass_by_an_ambiguous_barcode_is_notunique(coordinator):
    imported(coordinator, "firstname=A&lastname=B&carnumber=101")
    imported(coordinator, "firstname=C&lastname=D&carnumber=101")

    reply = act(coordinator, "action=racer.pass&barcode=PWD101")

    assert reply["outcome"]["code"] == "notunique"
    assert reply["reload"] is True


def test_pass_refuses_a_racer_with_results(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B")
    HeatLaneFactory(
        round=Round.objects.get(group=racer.group), racer=racer, finish_time=2.5
    )

    reply = act(coordinator, f"action=racer.pass&racer={racer.pk}&value=0")

    assert reply["outcome"]["code"] == "alreadyscheduled"
    assert reply["reload"] is True


# ----- racer.list -----


@pytest.fixture
def roster(coordinator, event):
    """Racers whose orders differ: by name Akiyama, by car Asher."""
    rows = [
        ("Adolfo", "Asher", "Lions", "101"),
        ("Antoine", "Akiyama", "Bears", "303"),
        ("Ben", "Bittinger", "Lions", "106"),
        ("Freeman", "Fizer", "Bears", "333"),
    ]
    made = {}
    for first, last, partition, car in rows:
        made[last] = imported(
            coordinator,
            f"firstname={first}&lastname={last}&partition={partition}&carnumber={car}",
        )
    return made


@pytest.mark.parametrize(
    ("order", "lastnames"),
    [
        ("", ["Akiyama", "Asher", "Bittinger", "Fizer"]),
        ("name", ["Akiyama", "Asher", "Bittinger", "Fizer"]),
        ("class", ["Asher", "Bittinger", "Akiyama", "Fizer"]),
        ("rank", ["Asher", "Bittinger", "Akiyama", "Fizer"]),
        ("car", ["Asher", "Bittinger", "Akiyama", "Fizer"]),
    ],
)
def test_list_orders(coordinator, roster, order, lastnames):
    racers = get(coordinator, f"racer.list&order={order}")["racers"]

    assert [r["lastname"] for r in racers] == lastnames


def test_list_by_checkin_puts_the_latest_first(coordinator, roster):
    for name in ("Asher", "Fizer"):
        act(coordinator, f"action=racer.pass&racer={roster[name].pk}")

    racers = get(coordinator, "racer.list&order=checkin")["racers"]

    assert [r["lastname"] for r in racers][:2] == ["Fizer", "Asher"]
    assert racers[0]["seq"] > racers[1]["seq"] > 0


def test_list_row_fields(coordinator, roster):
    racer = roster["Asher"]

    (row,) = get(coordinator, f"racer.list&racerid={racer.pk}")["racers"]

    assert row == {
        "racerid": racer.pk,
        "firstname": "Adolfo",
        "lastname": "Asher",
        "carnumber": 101,
        "carname": "",
        "partitionid": racer.partition_id,
        "partition": "Lions",
        "note_from": "",
        "headshot": "",
        "car_photo": "",
        "classid": racer.group_id,
        "class": "Lions",
        "rankid": racer.subgroup_id,
        "rank": "Lions",
        "passed": False,
        "seq": 0,
    }


def test_list_by_walk_in_barcode_creates_the_racer(coordinator, roster, event):
    reply = get(coordinator, "racer.list&barcode=PWDwi305")

    walk_in = Racer.objects.get(event=event, car_number=305)
    assert list(reply) == ["new-row", "car-numbers", "racers"]
    assert reply["new-row"]["racerid"] == walk_in.pk
    assert reply["new-row"]["lastname"] == 305
    assert [r["racerid"] for r in reply["racers"]] == [walk_in.pk]
    assert walk_in.partition.name == "Bears"


def test_list_by_barcode_needs_check_in(client, roster, event):
    log_in(client, Perm.JUDGING)

    reply = get(client, "racer.list&barcode=PWDwi305")

    assert reply["outcome"]["code"] == "notauthorized"
    assert "racers" not in reply
    assert not Racer.objects.filter(event=event, car_number=305).exists()


def test_list_needs_a_racer_role(client, roster):
    client.post("/action.php", {"action": "role.login", "name": ""})

    assert get(client, "racer.list")["outcome"]["code"] == "notauthorized"


def test_list_racers_ignores_the_other_event(coordinator, roster, event):
    assert all(
        r["racerid"] in {x.pk for x in roster.values()}
        for r in get(coordinator, "racer.list")["racers"]
    )


# ----- racer.fake -----


def test_fake_racers_get_pictures(coordinator, event, tmp_path):
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers"),
        car_photo_directory=str(tmp_path / "cars"),
    )

    reply = act(
        coordinator, "action=racer.fake&ngroups=2&racers_per_group=4&check_in=1"
    )

    assert reply["outcome"]["code"] == "success"
    made = Racer.objects.filter(event=event)
    assert {r.partition.name for r in made} == {"Fake Lions", "Fake Tigers"}
    assert all(r.passed_inspection for r in made)
    for racer in made:
        assert racer.headshot == f"fake_{racer.pk}.svg"
        assert (tmp_path / "racers" / racer.headshot).exists()
        assert (tmp_path / "cars" / racer.car_photo).exists()
    row = get(coordinator, f"racer.list&racerid={made[0].pk}")["racers"][0]
    assert row["headshot"].startswith(
        f"photo.php/head/file/200x200/fake_{made[0].pk}.svg/"
    )
    assert (
        get(coordinator, "settings.list&key=fake-racers")["settings"]["fake-racers"]
        == 1
    )
    assert HeatLane.objects.count() == 0


# ----- review fixes -----


def test_a_refused_delete_is_still_logged(coordinator, event):
    """PHP records `racer.*delete` before it checks the outcome."""
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions")
    HeatLaneFactory(round=Round.objects.get(group=racer.group), racer=racer)

    act(coordinator, f"action=racer.delete&racer={racer.pk}")

    assert ActionLog.objects.filter(
        event=event, request={"action": "racer.*delete", "racerid": str(racer.pk)}
    ).exists()


def test_a_refused_bulk_still_reloads(coordinator):
    assert act(coordinator, "action=racer.bulk&who=all&what=x")["reload"] is True
    assert act(coordinator, "action=racer.bulk&who=x&what=checkin")["reload"] is True


def test_an_unknown_rankid_alone_sends_no_partition_sortorder(coordinator):
    racer = imported(coordinator, "firstname=A&lastname=B&partition=Lions")

    reply = act(coordinator, f"action=racer.edit&racer={racer.pk}&rankid=-1")

    assert reply["outcome"]["code"] == "noclass"
    assert "partition-sortorder" not in reply


def test_an_anonymous_bulk_is_refused_with_reload(client, coordinator, event):
    racer = imported(coordinator, "firstname=A&lastname=B")
    client.post("/action.php", {"action": "role.login", "name": ""})

    reply = act(client, "action=racer.bulk&who=all&what=checkin")

    assert (reply["outcome"]["code"], reply["reload"]) == ("notauthorized", True)
    assert not Racer.objects.get(pk=racer.pk).passed_inspection


def test_a_full_partition_refuses_a_blank_car_number(coordinator, event):
    groups.find_or_create_partition(event.id, "Lions")
    groups.find_or_create_partition(event.id, "Tigers")
    imported(coordinator, "firstname=T&lastname=T&partition=Tigers&carnumber=201")
    for car in range(101, 201):
        imported(coordinator, f"firstname=A&lastname=B&partition=Lions&carnumber={car}")

    reply = act(
        coordinator, "action=racer.import&firstname=C&lastname=D&partition=Lions"
    )

    assert reply["outcome"]["code"] == "no-carnumber"
