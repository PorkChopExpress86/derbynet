"""The check-in page's views (#42): the guard and the redirects, the poll
and its 204, the filters, pass and un-pass, the D36 question, the D32 final
request, the racer sheet with car numbers and ranges (D47, D48), bulk
changes, photos, and the QR code."""

import io
import re
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.permissions import SESSION_PERMISSIONS, SESSION_ROLE, Perm
from accounts.views import make_mcheckin_token
from awards.models import Award
from core.models import RaceSettings
from racing.models import FinalRequest, HeatLane, RosterEntry
from roster.models import CarRange, Partition, Racer
from roster.services.racers import pass_racer
from roster.services.weigh_in import record_weigh_in, set_partition_weigh_in
from ui.tests.db import no_db_writes
from ui.tests.race import add_racers, chart, race_setup, run_heat, scheduled
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


@pytest.fixture
def crew(login):
    return login("RaceCrew")


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


def post(client, url, data=None, **extra):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", **extra)


def get(client, url, data=None):
    return client.get(url, data or {}, HTTP_HX_REQUEST="true")


def signature(html: str) -> str:
    return re.search(r'id="checkin-sig" name="sig" value="([^"]+)"', html).group(1)


def card_ids(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'data-racer="(\d+)"', html)]


def as_permissions(client, bits):
    session = client.session
    session[SESSION_ROLE] = "Tester"
    session[SESSION_PERMISSIONS] = int(bits)
    session.save()
    return client


# ----- the page, the guard, the redirects -----


def test_the_page_needs_check_in_racers(login, event):
    assert login("RaceCrew").get("/checkin/").status_code == 200
    assert login("RaceCoordinator").get("/checkin/").status_code == 200
    visitor = login("anonymous").get("/checkin/")
    assert visitor.status_code == 302 and "/login/" in visitor["Location"]
    assert post(login("anonymous"), "/checkin/racer/new/").status_code == 403


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/checkin.php", "/checkin/"),
        ("/checkin.php?order=car", "/checkin/?order=car"),
        ("/mcheckin.php?tok=abc", "/checkin/?tok=abc"),
        ("/derbynet/mcheckin.php?tok=abc", "/checkin/?tok=abc"),
    ],
)
def test_the_php_pages_move_to_check_in(client, event, old, new):
    response = client.get(old)
    assert response.status_code == 301
    assert response["Location"] == new


def test_the_qr_link_through_mcheckin_php_opens_check_in(client, event):
    token = make_mcheckin_token()
    moved = client.get(f"/mcheckin.php?tok={token}")
    opened = client.get(moved["Location"])

    assert opened.status_code == 302 and opened["Location"] == "/checkin/"
    assert client.get("/checkin/").status_code == 200


def test_the_qr_sheet_hands_out_a_working_link(crew, client, event):
    html = get(crew, "/checkin/qr/").content.decode()
    url = re.search(r"data-qr-url>([^<]+)<", html).group(1)
    assert "/checkin/?tok=" in url

    path = url.split("testserver", 1)[1]
    assert client.get(path)["Location"] == "/checkin/"
    assert client.get("/checkin/").status_code == 200


def test_only_a_role_with_every_check_in_right_gets_the_qr_code(client, event):
    as_permissions(client, Perm.CHECK_IN_RACERS)
    assert get(client, "/checkin/qr/").status_code == 422


# ----- the list and its poll -----


def test_the_poll_answers_204_while_nothing_changed(crew, event):
    racers = add_racers(event, "Wolves", 3, 101, passed=False)
    html = crew.get("/checkin/").content.decode()
    sig = signature(html)

    with no_session_writes(), no_db_writes():
        assert get(crew, "/checkin/list/", {"sig": sig}).status_code == 204
    pass_racer(racers[0], True)
    with no_session_writes(), no_db_writes():
        changed = get(crew, "/checkin/list/", {"sig": sig})
    assert changed.status_code == 200
    assert signature(changed.content.decode()) != sig


def test_search_narrows_by_name_or_car_number(crew, event):
    wolves = add_racers(event, "Wolves", 3, 101)
    bears = add_racers(event, "Bears", 2, 201)
    Racer.objects.filter(pk=bears[1].pk).update(first_name="Zelda", last_name="Quick")

    def shown(**filters):
        return card_ids(get(crew, "/checkin/list/", filters).content.decode())

    assert shown(q="202") == [bears[1].pk]
    assert shown(q="10") == [r.pk for r in wolves]
    assert shown(q="zel") == [bears[1].pk]
    assert shown(q="quick, z") == [bears[1].pk]
    assert shown(q="nobody") == []


def test_filters_and_sort_orders(crew, event):
    wolves = add_racers(event, "Wolves", 2, 102, passed=False)
    bears = add_racers(event, "Bears", 2, 101)
    Racer.objects.filter(pk=bears[1].pk).update(car_number=110)
    pass_racer(wolves[0], True)
    wolves_partition = wolves[0].partition_id

    def shown(**filters):
        return card_ids(get(crew, "/checkin/list/", filters).content.decode())

    assert shown(partition=wolves_partition) == [r.pk for r in wolves]
    assert shown(status="waiting") == [wolves[1].pk]
    assert set(shown(status="passed")) == {wolves[0].pk, *[r.pk for r in bears]}
    assert shown(order="car")[:2] == [bears[0].pk, wolves[0].pk]
    # Bears is the second partition, so by partition the Wolves come first.
    assert shown(order="partition") == [*[r.pk for r in wolves], *[r.pk for r in bears]]
    assert shown(order="class")[:2] == [r.pk for r in wolves]
    by_name = shown(order="name")
    assert by_name[0] == bears[0].pk  # "Bears1 Racer" sorts by last name, then first.


def test_the_count_follows_the_den_filter(crew, event):
    wolves = add_racers(event, "Wolves", 3, 101, passed=False)
    add_racers(event, "Bears", 2, 201)
    pass_racer(wolves[0], True)

    html = get(crew, "/checkin/list/", {"partition": wolves[0].partition_id}).content
    assert b'<strong class="text-ink">1</strong> of 3 passed' in html
    html = get(crew, "/checkin/list/").content
    assert b'<strong class="text-ink">3</strong> of 5 passed' in html


def test_the_card_carries_every_php_column(crew, event):
    RaceSettings.objects.filter(event=event).update(use_xbs=True)
    racer = add_racers(event, "Wolves", 1, 7, passed=False)[0]
    Racer.objects.filter(pk=racer.pk).update(car_name="Zoom", note="Pack 12")
    html = get(crew, "/checkin/list/").content.decode()

    assert "data-car>007<" in html  # D47
    assert "data-partition>Wolves<" in html
    assert "Wolves1" in html and "Racer" in html
    assert "data-car-name>Zoom<" in html and "from Pack 12" in html
    assert 'data-photo="head"' in html and 'data-photo="car"' in html
    assert 'aria-label="Passed, Wolves1 Racer"' in html
    assert 'aria-label="Exclusively By Scout, Wolves1 Racer"' in html


# ----- pass and un-pass -----


def test_pass_and_unpass_with_undo(crew, event):
    racer = add_racers(event, "Wolves", 1, 101, passed=False)[0]

    passed = post(crew, f"/checkin/racer/{racer.pk}/pass/", {"passed": "1"})
    assert passed.status_code == 200 and "checkin-refresh" in passed["HX-Trigger"]
    racer.refresh_from_db()
    assert racer.passed_inspection

    unpassed = post(crew, f"/checkin/racer/{racer.pk}/pass/", {"passed": "0"})
    racer.refresh_from_db()
    assert not racer.passed_inspection
    undo = re.search(r'hx-post="([^"]+)"', unpassed.content.decode()).group(1)
    assert undo == f"/checkin/racer/{racer.pk}/pass/?passed=1&amp;undo=1"
    post(crew, undo.replace("&amp;", "&"))
    racer.refresh_from_db()
    assert racer.passed_inspection


def test_unpass_needs_revert_check_in(client, event):
    racer = add_racers(event, "Wolves", 1, 101)[0]
    as_permissions(client, Perm.CHECK_IN_RACERS)

    refused = post(client, f"/checkin/racer/{racer.pk}/pass/", {"passed": "0"})
    assert refused.status_code == 422
    racer.refresh_from_db()
    assert racer.passed_inspection
    # The card shows a pill, not a switch, to such a role.
    html = get(client, "/checkin/list/").content.decode()
    assert 'role="switch"' not in html and "Passed" in html


def test_a_racer_with_a_result_cannot_be_unpassed(coordinator, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)
    raced = HeatLane.objects.filter(round=wolves, heat=1).first().racer

    refused = post(coordinator, f"/checkin/racer/{raced.pk}/pass/", {"passed": "0"})
    assert refused.status_code == 422
    assert (
        'data-state="ok"'
        in get(
            coordinator, "/checkin/list/", {"q": str(raced.car_number)}
        ).content.decode()
    )


# ----- D36: the question, and D32: the final request -----


@pytest.fixture
def running(event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    run_heat(event)
    late = add_racers(event, "Wolves", 1, 150, passed=False)[0]
    return wolves, late


def test_a_pass_during_a_round_asks_the_question(crew, running):
    wolves, late = running
    reply = post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "1"})
    html = reply.content.decode()

    assert f'data-question="{late.pk}"' in html
    assert (
        "Wolves, Round 1</strong> is in progress. Add this car to the current round?"
        in html
    )
    # The card's switch posts to #toasts, so the question comes out of band.
    assert 'id="checkin-sheet-body" hx-swap-oob="innerHTML"' in html


def test_yes_fits_the_car_into_the_remaining_heats(crew, running):
    wolves, late = running
    post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "1"})
    before = chart(wolves)

    reply = post(crew, f"/checkin/racer/{late.pk}/round/yes/")
    assert reply.status_code == 200, reply.content
    after = chart(wolves)
    assert after[:2] == before[:2]
    assert any(150 in heat for heat in after)


def test_no_leaves_the_chart_and_the_card_says_so(crew, running):
    wolves, late = running
    post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "1"})
    before = chart(wolves)

    assert post(crew, f"/checkin/racer/{late.pk}/round/no/").status_code == 200
    assert chart(wolves) == before
    assert RosterEntry.objects.get(round=wolves, racer=late).left_out_at
    html = get(crew, "/checkin/list/", {"q": "150"}).content.decode()
    assert "Not added to Wolves, Round 1" in html
    # A second pass asks nothing more: the race coordinator decides now.
    post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "0"})
    again = post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "1"})
    assert "data-question" not in again.content.decode()


def test_an_unanswered_late_card_asks_again(crew, running):
    wolves, late = running
    pass_racer(late, True)
    html = get(crew, "/checkin/list/", {"q": "150"}).content.decode()
    assert "Late! Wolves, Round 1 is in progress." in html
    sheet = get(crew, f"/checkin/racer/{late.pk}/round/").content.decode()
    assert f'data-question="{late.pk}"' in sheet


def test_a_racer_after_round_1_requests_a_place_in_the_final(crew, event):
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves)
    for _ in range(
        HeatLane.objects.filter(round=wolves).values("heat").distinct().count()
    ):
        run_heat(event)
    late = add_racers(event, "Wolves", 1, 150)[0]

    html = get(crew, "/checkin/list/", {"q": "150"}).content.decode()
    assert 'data-final="can"' in html and "Request place in final" in html
    assert "Late!" not in html

    assert post(crew, f"/checkin/racer/{late.pk}/final/").status_code == 200
    request = FinalRequest.objects.get(racer=late)
    assert request.state == FinalRequest.State.REQUESTED
    html = get(crew, "/checkin/list/", {"q": "150"}).content.decode()
    assert 'data-final="requested"' in html
    assert "Not eligible for speed awards" in html
    sheet = get(crew, f"/checkin/racer/{late.pk}/").content.decode()
    assert re.search(r'name="no_speed" value="1" checked', sheet)


# ----- the racer sheet -----


def test_the_first_racer_of_an_empty_roster_is_101_in_default(coordinator, event):
    sheet = get(coordinator, "/checkin/racer/new/").content.decode()
    assert '<option value="">Default</option>' in sheet
    assert 'value="101"' in sheet

    reply = post(
        coordinator,
        "/checkin/racer/new/",
        {
            "firstname": "Fred",
            "lastname": "Flintstone",
            "carno": "101",
            "partitionid": "",
        },
    )
    assert "Added 101 Fred Flintstone." in reply.content.decode()
    racer = Racer.objects.get(event=event)
    assert (racer.car_number, racer.partition.name) == (101, "Default")


def test_the_add_sheet_lists_exactly_the_partitions_and_suggests_their_number(
    crew, event
):
    add_racers(event, "Scouts", 2, 101)
    add_racers(event, "Siblings", 1, 201)
    sheet = get(crew, "/checkin/racer/new/").content.decode()
    options = re.findall(r'<option value="(\d*)"[^>]*>([^<]+)</option>', sheet)
    assert [name for _, name in options] == ["Scouts", "Siblings"]
    assert 'value="103"' in sheet

    siblings = Partition.objects.get(event=event, name="Siblings")
    hint = get(crew, "/checkin/number/", {"partitionid": siblings.pk}).content.decode()
    assert 'data-suggested="202"' in hint
    touched = get(
        crew,
        "/checkin/number/",
        {"partitionid": siblings.pk, "touched": "1", "carno": "101"},
    ).content.decode()
    assert "data-suggested" not in touched
    assert "Car number 101 belongs to Scouts1 Racer too." in touched


def test_a_typed_partition_name_is_never_accepted(crew, event):
    add_racers(event, "Scouts", 1, 101)
    reply = post(
        crew,
        "/checkin/racer/new/",
        {"firstname": "A", "lastname": "B", "carno": "", "partitionid": "Bears"},
    )
    assert "Pick the racer&#x27;s class." in reply.content.decode()
    assert not Partition.objects.filter(name="Bears").exists()


def test_a_duplicate_number_saves_with_its_warning_and_the_sheet_stays(crew, event):
    add_racers(event, "Scouts", 1, 101)
    scouts = Partition.objects.get(event=event, name="Scouts")
    reply = post(
        crew,
        "/checkin/racer/new/",
        {
            "firstname": "Dup",
            "lastname": "Car",
            "carno": "101",
            "partitionid": scouts.pk,
        },
    )
    html = reply.content.decode()
    assert "data-warnings" in html and "Duplicate car number 101" in html
    assert "data-racer-form" in html and 'data-action="take-car-photo"' in html
    assert Racer.objects.filter(event=event, car_number=101).count() == 2


def test_the_number_is_posted_plain_and_shown_padded(crew, event):
    add_racers(event, "Scouts", 1, 101)
    scouts = Partition.objects.get(event=event, name="Scouts")
    post(
        crew,
        "/checkin/racer/new/",
        {
            "firstname": "Low",
            "lastname": "Number",
            "carno": "042",
            "partitionid": scouts.pk,
        },
    )
    racer = Racer.objects.get(event=event, first_name="Low")
    assert racer.car_number == 42
    assert 'value="042"' in get(crew, f"/checkin/racer/{racer.pk}/").content.decode()
    bad = post(
        crew,
        "/checkin/racer/new/",
        {"firstname": "X", "lastname": "Y", "carno": "4a", "partitionid": scouts.pk},
    )
    assert "must be a whole number" in bad.content.decode()


def _full_ranges(event):
    add_racers(event, "Scouts", 1, 101)
    scouts = Partition.objects.get(event=event, name="Scouts")
    CarRange.objects.create(
        event=event, partition=scouts, first=101, last=101, sort_order=1
    )
    return scouts


def test_range_full_asks_a_set_up_login_for_another_range(coordinator, event):
    scouts = _full_ranges(event)
    reply = post(
        coordinator,
        "/checkin/racer/new/",
        {"firstname": "A", "lastname": "B", "carno": "", "partitionid": scouts.pk},
    )
    html = reply.content.decode()
    assert "Every car number range of Scouts is taken." in html
    assert 'data-action="add-range"' in html

    added = post(
        coordinator,
        f"/checkin/partition/{scouts.pk}/range/",
        {
            "range_first": "400",
            "range_last": "499",
            "firstname": "A",
            "partitionid": scouts.pk,
        },
    )
    assert added.status_code == 200
    assert 'value="400"' in added.content.decode()
    assert CarRange.objects.filter(partition=scouts, first=400, last=499).exists()


def test_range_full_tells_others_to_ask_the_race_coordinator(crew, event):
    scouts = _full_ranges(event)
    hint = get(crew, "/checkin/number/", {"partitionid": scouts.pk}).content.decode()
    assert "Ask the race coordinator for another range." in hint
    assert "add-range" not in hint
    refused = post(
        crew,
        f"/checkin/partition/{scouts.pk}/range/",
        {"range_first": "400", "range_last": "499"},
    )
    assert refused.status_code == 422


def test_an_out_of_range_number_is_warned_about(crew, event):
    scouts = _full_ranges(event)
    hint = get(
        crew,
        "/checkin/number/",
        {"partitionid": scouts.pk, "carno": "350", "touched": "1"},
    ).content.decode()
    assert "outside the car number ranges of Scouts" in hint


def test_edit_saves_the_partition_and_both_award_settings(crew, event):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    add_racers(event, "Siblings", 1, 201)
    siblings = Partition.objects.get(event=event, name="Siblings")
    reply = post(
        crew,
        f"/checkin/racer/{racer.pk}/",
        {
            "firstname": "New",
            "lastname": "Name",
            "carname": "Zoom",
            "note_from": "Pack 9",
            "carno": "105",
            "partitionid": siblings.pk,
            "exclude": "1",
            "no_speed": "1",
        },
    )
    assert reply.status_code == 200
    racer.refresh_from_db()
    assert (racer.first_name, racer.car_name, racer.note, racer.car_number) == (
        "New",
        "Zoom",
        "Pack 9",
        105,
    )
    assert racer.partition_id == siblings.pk
    assert racer.exclude and racer.not_eligible_for_speed_awards


def test_delete_confirms_and_refuses_a_scheduled_racer(crew, event):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    confirm = get(crew, f"/checkin/racer/{racer.pk}/delete/").content.decode()
    assert "Delete 1 racer" in confirm
    post(crew, f"/checkin/racer/{racer.pk}/delete/")
    assert not Racer.objects.filter(pk=racer.pk).exists()

    wolves = race_setup(event)["Wolves"]
    scheduled(wolves, race=False)
    busy = HeatLane.objects.filter(round=wolves).first().racer
    assert post(crew, f"/checkin/racer/{busy.pk}/delete/").status_code == 422


def test_xbs_switch(crew, event):
    RaceSettings.objects.filter(event=event).update(use_xbs=True)
    racer = add_racers(event, "Scouts", 1, 101)[0]
    post(crew, f"/checkin/racer/{racer.pk}/xbs/", {"xbs": "1"})
    assert Award.objects.filter(racer=racer, name="Exclusively By Scout").exists()
    post(crew, f"/checkin/racer/{racer.pk}/xbs/", {"xbs": "0"})
    assert not Award.objects.filter(racer=racer).exists()


# ----- bulk -----


def test_bulk_check_in_undo_renumber_and_eligibility(coordinator, event):
    scouts = add_racers(event, "Scouts", 2, 101, passed=False)
    add_racers(event, "Siblings", 1, 201, passed=False)
    who = f"d{scouts[0].partition_id}"

    def bulk(**data):
        return post(coordinator, "/checkin/bulk/", data)

    assert bulk(who=who, what="checkin").status_code == 200
    assert list(Racer.objects.filter(passed_inspection=True).order_by("pk")) == scouts
    bulk(who="all", what="uncheckin")
    assert not Racer.objects.filter(passed_inspection=True).exists()
    bulk(who="all", what="number", numbering="custom", start="500")
    assert sorted(Racer.objects.values_list("car_number", flat=True)) == [500, 501, 502]
    bulk(who="all", what="number", numbering="standard")
    assert sorted(Racer.objects.values_list("car_number", flat=True)) == [101, 102, 201]
    bulk(who=who, what="eligibility", eligible="0")
    assert set(Racer.objects.filter(exclude=True)) == set(scouts)
    assert not Racer.objects.filter(not_eligible_for_speed_awards=True).exists()


def test_bulk_undo_needs_revert_check_in(client, event):
    add_racers(event, "Scouts", 2, 101)
    as_permissions(client, Perm.CHECK_IN_RACERS | Perm.EDIT_RACER)
    assert (
        post(client, "/checkin/bulk/", {"who": "all", "what": "uncheckin"}).status_code
        == 422
    )
    assert Racer.objects.filter(passed_inspection=True).count() == 2


# ----- photos -----


@pytest.fixture
def folders(event, tmp_path):
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers"),
        car_photo_directory=str(tmp_path / "cars"),
    )
    (tmp_path / "racers").mkdir()
    (tmp_path / "cars").mkdir()
    return tmp_path


def jpeg(size=(600, 800)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(stream, "JPEG")
    return stream.getvalue()


def test_capture_and_check_in_stores_the_car_photo_and_passes(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101, passed=False)[0]
    sheet = get(crew, f"/checkin/racer/{racer.pk}/photo/car/").content.decode()
    assert 'accept="image/*" capture="environment"' in sheet

    reply = post(
        crew,
        f"/checkin/racer/{racer.pk}/photo/car/upload/",
        {"photo": SimpleUploadedFile("car.jpg", jpeg()), "checkin": "1"},
    )
    html = reply.content.decode()
    assert reply.status_code == 200, html
    racer.refresh_from_db()
    assert racer.car_photo == "car.jpg" and racer.passed_inspection
    assert 'data-crop-sheet="car"' in html
    assert "/photo.php/car/file/work/car.jpg/" in html


def test_crop_and_rotate(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    post(
        crew,
        f"/checkin/racer/{racer.pk}/photo/car/upload/",
        {"photo": SimpleUploadedFile("car.jpg", jpeg()), "checkin": "0"},
    )
    turned = post(crew, f"/checkin/racer/{racer.pk}/photo/car/rotate/left/")
    assert 'data-crop-sheet="car"' in turned.content.decode()
    box = {
        "left": "0",
        "top": "0",
        "right": "300",
        "bottom": "400",
        "original_width": "600",
        "original_height": "450",
    }
    assert (
        post(crew, f"/checkin/racer/{racer.pk}/photo/car/crop/save/", box).status_code
        == 200
    )
    racer.refresh_from_db()
    assert racer.car_photo == "0-cropped/car.jpg"
    assert (folders / "cars" / "0-cropped" / "car.jpg").is_file()
    assert (
        post(crew, f"/checkin/racer/{racer.pk}/photo/car/crop/save/", {}).status_code
        == 422
    )


def test_an_upload_with_no_photo_is_refused(crew, event, folders):
    racer = add_racers(event, "Scouts", 1, 101)[0]
    assert post(crew, f"/checkin/racer/{racer.pk}/photo/car/upload/").status_code == 422


# ----- review: the status filter -----


def test_the_status_filter_picks_racing_late_and_scheduled_but_not_passed(crew, event):
    """The filter bar's status, beside passed or not: the status text PHP's
    table shows ("Racing!", "Scheduled but not passed", "Late!")."""
    wolves = race_setup(event)["Wolves"]
    scheduled(wolves, race=False)
    unpassed = HeatLane.objects.filter(round=wolves).first().racer
    Racer.objects.filter(pk=unpassed.pk).update(passed_inspection=False)
    late = add_racers(event, "Wolves", 1, 150)[0]
    waiting = add_racers(event, "Bears", 1, 201, passed=False)[0]

    def shown(state):
        return set(
            card_ids(get(crew, "/checkin/list/", {"state": state}).content.decode())
        )

    racing = set(
        HeatLane.objects.filter(round=wolves)
        .exclude(racer=unpassed)
        .values_list("racer_id", flat=True)
    )
    assert shown("racing") == racing
    assert shown("unpassed") == {unpassed.pk}
    assert shown("late") == {late.pk}
    assert shown("unscheduled") == {waiting.pk}
    assert len(shown("")) == len(racing) + 3
    assert 'name="state"' in crew.get("/checkin/").content.decode()


# ----- review: the award settings save with the racer -----


def _award_step_fails(*args, **kwargs):
    raise RuntimeError("award settings failed")


def test_a_failed_award_step_stores_no_new_racer(crew, event, monkeypatch):
    from roster.services import racers

    monkeypatch.setattr(racers, "_store_award_settings", _award_step_fails)
    with pytest.raises(RuntimeError):
        post(
            crew,
            "/checkin/racer/new/",
            {"firstname": "A", "lastname": "B", "carno": "101", "no_speed": "1"},
        )
    assert not Racer.objects.filter(event=event).exists()


def test_a_failed_award_step_leaves_the_edit_unsaved(crew, event, monkeypatch):
    from roster.services import racers

    racer = add_racers(event, "Scouts", 1, 101)[0]
    monkeypatch.setattr(racers, "_store_award_settings", _award_step_fails)
    with pytest.raises(RuntimeError):
        post(
            crew,
            f"/checkin/racer/{racer.pk}/",
            {
                "firstname": "Changed",
                "lastname": "Racer",
                "carno": "101",
                "partitionid": racer.partition_id,
                "no_speed": "1",
            },
        )
    racer.refresh_from_db()
    assert racer.first_name == "Scouts1"


# ----- review: Undo restores, and asks nothing -----


def test_undo_of_an_unpass_does_not_ask_the_question_again(crew, running):
    wolves, late = running
    post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "1"})
    unpassed = post(crew, f"/checkin/racer/{late.pk}/pass/", {"passed": "0"})
    undo = re.search(r'hx-post="([^"]+)"', unpassed.content.decode()).group(1)

    restored = post(crew, undo.replace("&amp;", "&")).content.decode()
    late.refresh_from_db()
    assert late.passed_inspection
    assert "data-question" not in restored
    # The card still offers the question to whoever wants to answer it.
    html = get(crew, "/checkin/list/", {"q": "150"}).content.decode()
    assert 'data-action="ask"' in html


# ----- the weigh-in (#70, D8, D54) -----


def weighs(event, partition_name, mode, unit="", max_weight=None, n=1, first=301):
    racers = add_racers(event, partition_name, n, first, passed=False)
    set_partition_weigh_in(racers[0].partition, mode, unit, max_weight)
    return racers


def card_html(client, racer) -> str:
    html = get(client, "/checkin/list/").content.decode()
    found = re.search(rf'<li [^>]*data-racer="{racer.pk}".*?</li>', html, re.S)
    return found.group(0)


@pytest.mark.parametrize(
    ("unit", "typed", "shown"),
    [
        ("oz", "5.0", "5.0 oz"),
        ("g", "141.75", "141.75 g"),
        ("lb", "0.3125", "0.3125 lb"),
    ],
)
def test_a_recorded_weight_is_stored_in_grams_and_shown_on_the_card(
    crew, event, unit, typed, shown
):
    racer = weighs(event, "Outlaws", "recorded", unit, first=7)[0]
    reply = post(crew, f"/checkin/racer/{racer.pk}/weigh/", {"weight": typed})
    assert reply.status_code == 200
    assert f"007 Outlaws1 Racer weighs {shown}." in reply.content.decode()
    racer.refresh_from_db()
    assert racer.weight_grams == Decimal("141.75")
    card = card_html(crew, racer)
    assert f'<span class="text-sm font-semibold" data-weight>{shown}</span>' in card
    assert "data-car>007<" in card  # D47


def test_pass_fail_shows_pass_and_fail_and_records_the_choice(crew, event):
    racer = weighs(event, "Scouts", "pass_fail")[0]
    card = card_html(crew, racer)
    assert 'data-weigh-in="pass_fail"' in card and "Not weighed" in card
    assert 'data-action="weigh-pass"' in card and 'data-action="weigh-fail"' in card
    assert "data-weight-input" not in card
    reply = post(crew, f"/checkin/racer/{racer.pk}/weigh/", {"passed": "0"})
    assert "weigh-in failed." in reply.content.decode()
    racer.refresh_from_db()
    assert (racer.weigh_in_passed, racer.weight_grams) == (False, None)
    assert "Failed" in card_html(crew, racer)


def test_a_partition_with_no_weigh_in_shows_no_controls(crew, event):
    racer = add_racers(event, "Tigers", 1, 101, passed=False)[0]
    assert "data-weigh-in" not in card_html(crew, racer)
    sheet = get(crew, f"/checkin/racer/{racer.pk}/").content.decode()
    assert "data-weigh-in" not in sheet and "data-weigh-fields" not in sheet
    page = crew.get("/checkin/").content.decode()
    assert 'name="weigh"' not in page
    refused = post(crew, f"/checkin/racer/{racer.pk}/weigh/", {"passed": "1"})
    assert refused.status_code == 422
    assert "Tigers has no weigh-in" in refused.content.decode()


def test_a_weight_for_pass_fail_and_none_for_recorded_are_refused(crew, event):
    scout = weighs(event, "Scouts", "pass_fail")[0]
    outlaw = weighs(event, "Outlaws", "recorded", "oz", first=401)[0]
    assert (
        post(crew, f"/checkin/racer/{scout.pk}/weigh/", {"weight": "5"}).status_code
        == 422
    )
    assert (
        post(crew, f"/checkin/racer/{outlaw.pk}/weigh/", {"weight": " "}).status_code
        == 422
    )
    assert not Racer.objects.filter(weighed_at__isnull=False).exists()


def test_over_the_maximum_saves_warns_and_shows_a_chip(crew, event):
    racer = weighs(event, "Outlaws", "recorded", "oz", "5")[0]
    reply = post(crew, f"/checkin/racer/{racer.pk}/weigh/", {"weight": "5.3"})
    assert reply.status_code == 200
    assert "5.3 oz is over the 5.0 oz maximum for Outlaws." in reply.content.decode()
    racer.refresh_from_db()
    assert racer.weight_grams == Decimal("150.25")
    card = card_html(crew, racer)
    assert "data-over" in card and "Over the 5.0 oz max" in card


def test_the_sheet_weighs_and_answers_with_its_next_body(crew, event):
    racer = weighs(event, "Outlaws", "recorded", "oz", "5")[0]
    sheet = get(crew, f"/checkin/racer/{racer.pk}/").content.decode()
    assert 'id="weight-sheet-' in sheet
    reply = post(
        crew,
        f"/checkin/racer/{racer.pk}/weigh/",
        {"weight": "5.5"},
        HTTP_HX_TARGET="checkin-sheet-body",
    ).content.decode()
    assert "data-racer-sheet" in reply and "data-warnings" in reply
    assert "5.5 oz is over the 5.0 oz maximum" in reply


def test_the_add_sheet_asks_the_classs_weigh_in_and_saves_it(crew, event):
    outlaws = weighs(event, "Outlaws", "recorded", "oz", "5")[0].partition
    scouts = weighs(event, "Scouts", "pass_fail", first=401)[0].partition
    fields = get(crew, "/checkin/weigh/", {"partitionid": outlaws.pk}).content.decode()
    assert 'data-weigh-fields="recorded"' in fields and "(max 5.0 oz)" in fields
    fields = get(crew, "/checkin/weigh/", {"partitionid": scouts.pk}).content.decode()
    assert 'data-weigh-fields="pass_fail"' in fields
    assert (
        "data-weigh-fields"
        not in get(crew, "/checkin/weigh/", {"partitionid": "junk"}).content.decode()
    )

    def add(partition, **weigh):
        return post(
            crew,
            "/checkin/racer/new/",
            {
                "firstname": "New",
                "lastname": partition.name,
                "partitionid": partition.pk,
                "carno": "",
                **weigh,
            },
        ).content.decode()

    reply = add(outlaws, weight="5.25")
    assert "5.25 oz is over the 5.0 oz maximum" in reply
    assert Racer.objects.get(
        last_name="Outlaws", first_name="New"
    ).weight_grams == Decimal("148.83")
    add(scouts, weigh_passed="1")
    assert (
        Racer.objects.get(last_name="Scouts", first_name="New").weigh_in_passed is True
    )
    refused = add(scouts, weight="5")
    assert "data-sheet-error" in refused and "pass or fail" in refused
    assert Racer.objects.filter(last_name="Scouts", first_name="New").count() == 1


def test_the_weigh_in_filter(crew, event):
    scouts = weighs(event, "Scouts", "pass_fail", n=3)
    outlaws = weighs(event, "Outlaws", "recorded", "oz", "5", n=2, first=401)
    plain = add_racers(event, "Tigers", 1, 501, passed=False)
    record_weigh_in(scouts[0], passed=True)
    record_weigh_in(scouts[1], passed=False)
    record_weigh_in(outlaws[0], weight="5.5")

    def shown(weigh):
        return set(
            card_ids(get(crew, "/checkin/list/", {"weigh": weigh}).content.decode())
        )

    assert shown("weighed") == {scouts[0].pk, scouts[1].pk, outlaws[0].pk}
    assert shown("unweighed") == {scouts[2].pk, outlaws[1].pk}
    assert shown("failed") == {scouts[1].pk, outlaws[0].pk}
    assert plain[0].pk in shown("") and plain[0].pk in shown("junk")
    assert 'name="weigh"' in crew.get("/checkin/").content.decode()


def test_a_weigh_in_changes_the_poll_signature(crew, event):
    racer = weighs(event, "Outlaws", "recorded", "oz")[0]
    sig = signature(crew.get("/checkin/").content.decode())
    record_weigh_in(racer, weight="5")
    assert get(crew, "/checkin/list/", {"sig": sig}).status_code == 200


def test_no_legacy_endpoint_reads_or_writes_the_weigh_in(crew, event):
    """The legacy wire contract is unchanged (#70): `racer.list` answers the
    same before and after a weigh-in, and `racer.pass` leaves it alone."""
    racer = weighs(event, "Outlaws", "recorded", "oz", "5")[0]
    before = crew.get("/action.php", {"query": "racer.list"}).content
    record_weigh_in(racer, weight="5.5")
    assert crew.get("/action.php", {"query": "racer.list"}).content == before
    crew.post("/action.php", {"action": "racer.pass", "racer": racer.pk, "value": 1})
    racer.refresh_from_db()
    assert racer.passed_inspection and racer.weight_grams == Decimal("155.92")
