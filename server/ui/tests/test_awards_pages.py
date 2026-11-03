"""The award pages' views (#45): the awards editor, judging and the
presentation console. Guards and redirects, the editor's sheet with its
partition list (D35, D38), tap to assign with the refusal shown before the
tap (C31), remove with Undo, the ad hoc field, the 30 s and 1 s polls, the
no-kiosk warning, and Next, Reveal and Clear against PHP's award.current."""

import json
import re

import pytest

from awards import editing
from awards.listing import AD_HOC
from awards.models import Award, AwardPartition
from core.models import RaceState
from kiosks.models import Kiosk
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from roster.models import Group, Partition, Racer, Subgroup
from roster.services import groups
from roster.services.racers import insert_new_racer, pass_racer
from ui.tests.db import no_db_writes
from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db

DESIGN = 1


def post(client, url, data=None, target="toasts"):
    return client.post(url, data or {}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET=target)


def get(client, url, data=None):
    return client.get(url, data or {}, HTTP_HX_REQUEST="true")


def html(response) -> str:
    return response.content.decode()


def racer(event, name, partition, car, **flags) -> Racer:
    made = insert_new_racer(
        event,
        first_name=name,
        last_name="Racer",
        car_number=car,
        partition_name=partition,
        **flags,
    ).racer
    pass_racer(made, True)
    return Racer.objects.get(pk=made.pk)


@pytest.fixture
def pack(event):
    """One group, each partition a subgroup, subgroups on: Lions, Tigers
    and Parents, whose racer is not eligible for speed awards."""
    for name in ("Lions", "Tigers", "Parents"):
        groups.add_partition(event, name)
    groups.form_groups_by_rule(event, "one-group")
    from core.models import RaceSettings

    RaceSettings.objects.filter(event=event).update(use_subgroups=True)
    return {
        "lion": racer(event, "Lion", "Lions", 101),
        "tiger": racer(event, "Tiger", "Tigers", 201),
        "parent": racer(event, "Parent", "Parents", 301, no_speed_awards=True),
        "group": Group.objects.get(event=event),
    }


def subgroup(event, name) -> Subgroup:
    return Subgroup.objects.get(event=event, name=name)


def partition(event, name) -> Partition:
    return Partition.objects.get(event=event, name=name)


def award(event, name="Best Paint", **scope) -> Award:
    return Award.objects.create(event=event, name=name, award_type_id=DESIGN, **scope)


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


# ----- guards and redirects -----


@pytest.mark.parametrize(
    ("php", "page"),
    [
        ("awards-editor.php", "/awards/edit/"),
        ("judging.php?order=name", "/judging/?order=name"),
        ("awards-presentation.php", "/awards/present/"),
    ],
)
def test_the_php_pages_redirect_to_the_new_ones(client, php, page):
    response = client.get(f"/{php}")
    assert (response.status_code, response["Location"]) == (301, page)


@pytest.mark.parametrize(
    ("url", "status"),
    [("/judging/", 200), ("/awards/edit/", 403), ("/awards/present/", 403)],
)
def test_race_crew_judges_but_neither_edits_nor_presents(login, event, url, status):
    """RaceCrew has JUDGING only, of the three permissions."""
    response = login("RaceCrew").get(url)
    assert response.status_code in ((status,) if status == 200 else (302, 403))


def test_no_page_writes_on_get(coordinator, event, pack):
    """The award types come from a data migration, not a page load."""
    award(event)
    for url in ("/awards/edit/", "/judging/", "/awards/present/"):
        with no_session_writes(), no_db_writes():
            assert coordinator.get(url).status_code == 200


# ----- the awards editor -----


def test_the_editor_lists_awards_with_type_scope_and_partitions(
    coordinator, event, pack
):
    lions = editing.save_award(
        event,
        None,
        name="Lion Paint",
        award_type_id=4,
        group_id=pack["group"].pk,
        subgroup_id=subgroup(event, "Lions").pk,
        partition_ids=None,
    )
    editing.set_ad_hoc_award(event, pack["tiger"], "Most Creative")
    page = html(coordinator.get("/awards/edit/"))
    assert f'data-award="{lions.pk}"' in page
    assert "Design Trophy" in page and "Lions" in page
    assert "Most Creative" not in page
    assert 'href="/import/awards/"' in page


def test_the_sheet_offers_every_type_but_ad_hoc(coordinator, event, pack):
    sheet = html(get(coordinator, "/awards/edit/new/"))
    assert "Design General" in sheet and "Ad Hoc" not in sheet
    assert 'value="0,0"' in sheet
    assert f'value="{pack["group"].pk},{subgroup(event, "Lions").pk}"' in sheet


def test_a_new_award_saves_with_a_narrowed_list(coordinator, event, pack):
    response = post(
        coordinator,
        "/awards/edit/new/",
        {
            "name": "Best Paint",
            "awardtypeid": DESIGN,
            "class_and_rank": "0,0",
            "scope": "some",
            "partition": [partition(event, "Lions").pk, partition(event, "Tigers").pk],
        },
        target="awards-sheet-body",
    )
    assert response.status_code == 200
    assert 'id="awards-list" hx-swap-oob' in html(response)
    paint = Award.objects.get(event=event)
    assert set(
        AwardPartition.objects.filter(award=paint).values_list(
            "partition__name", flat=True
        )
    ) == {"Lions", "Tigers"}


def test_unchecking_the_last_box_is_refused_in_the_sheet(coordinator, event, pack):
    paint = award(event)
    response = post(
        coordinator,
        f"/awards/edit/{paint.pk}/",
        {"name": "Best Paint", "awardtypeid": DESIGN, "scope": "some"},
        target="awards-sheet-body",
    )
    assert "Check at least one" in html(response)
    assert 'data-award-sheet="' in html(response)
    assert not AwardPartition.objects.filter(award=paint).exists()


def test_the_partition_list_follows_the_group_picked(coordinator, event, pack):
    lions = subgroup(event, "Lions")
    found = html(
        get(
            coordinator,
            "/awards/edit/partitions/",
            {"class_and_rank": f"{pack['group'].pk},{lions.pk}"},
        )
    )
    # A subgroup award draws from one partition, so there is no list.
    assert "data-partition-list" not in found
    found = html(
        get(coordinator, "/awards/edit/partitions/", {"class_and_rank": "0,0"})
    )
    assert found.count('name="partition"') == 3


def test_delete_asks_first_then_deletes(coordinator, event, pack):
    paint = award(event)
    with no_db_writes():
        assert "Delete 1 award" in html(
            get(coordinator, f"/awards/edit/{paint.pk}/delete/")
        )
    response = post(
        coordinator, f"/awards/edit/{paint.pk}/delete/", target="awards-sheet-body"
    )
    assert response.status_code == 200
    assert not Award.objects.filter(pk=paint.pk).exists()
    assert post(coordinator, f"/awards/edit/{paint.pk}/delete/").status_code == 422


def test_reorder_saves_the_posted_order(coordinator, event, pack):
    a, b = award(event, "A"), award(event, "B")
    assert (
        post(
            coordinator, "/awards/edit/order/", {"order": f"{b.pk},{a.pk}"}
        ).status_code
        == 200
    )
    assert list(Award.objects.order_by("sort").values_list("name", flat=True)) == [
        "B",
        "A",
    ]
    assert (
        post(coordinator, "/awards/edit/order/", {"order": f"{a.pk}"}).status_code
        == 422
    )


# ----- judging -----


def test_judging_lists_passed_racers_marking_no_speed_awards(coordinator, event, pack):
    Racer.objects.filter(pk=pack["tiger"].pk).update(exclude=True)
    page = html(coordinator.get("/judging/"))
    assert f'data-racer="{pack["lion"].pk}"' in page
    assert f'data-racer="{pack["tiger"].pk}"' not in page
    parent = re.search(rf'data-racer="{pack["parent"].pk}".*?</li>', page, re.S)
    assert "data-speed-ineligible" in parent.group(0)
    assert ">101<" in page


@pytest.mark.parametrize(
    ("order", "cars"),
    [("car", ["101", "201", "301"]), ("name", ["101", "301", "201"])],
)
def test_the_sort_control_orders_the_grid(coordinator, event, pack, order, cars):
    board = html(get(coordinator, "/judging/board/", {"order": order}))
    assert re.findall(r"data-car>(\d+)<", board) == cars


def test_the_search_field_filters_the_grid(coordinator, event, pack):
    board = html(get(coordinator, "/judging/board/", {"q": "tig"}))
    assert re.findall(r"data-car>(\d+)<", board) == ["201"]


def test_the_board_answers_204_while_nothing_changed(coordinator, event, pack):
    board = html(get(coordinator, "/judging/board/"))
    sig = re.search(r'name="sig" value="(\w+)"', board).group(1)
    assert get(coordinator, "/judging/board/", {"sig": sig}).status_code == 204
    editing.assign_award(event, award(event).pk, pack["lion"].pk)
    assert get(coordinator, "/judging/board/", {"sig": sig}).status_code == 200


def test_the_racer_sheet_shows_why_an_award_is_closed(coordinator, event, pack):
    """C31: the subgroup check shows in the sheet before the judge taps."""
    lions = award(
        event, "Lion Paint", group=pack["group"], subgroup=subgroup(event, "Lions")
    )
    paint = award(event, "Best Paint")
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    sheet = html(get(coordinator, f"/judging/racer/{pack['tiger'].pk}/"))
    closed = re.search(rf'data-choice="{lions.pk}".*?</li>', sheet, re.S).group(0)
    assert "For Lions only" in closed and "disabled" in closed
    filled = re.search(rf'data-choice="{paint.pk}".*?</li>', sheet, re.S).group(0)
    assert "Given to 101 Lion Racer" in filled


def test_the_award_sheet_lists_only_racers_who_can_win(coordinator, event, pack):
    paint = editing.save_award(
        event,
        None,
        name="Best Paint",
        award_type_id=DESIGN,
        group_id=None,
        subgroup_id=None,
        partition_ids=[partition(event, "Lions").pk, partition(event, "Tigers").pk],
    )
    sheet = html(get(coordinator, f"/judging/award/{paint.pk}/"))
    assert re.findall(r'<li data-racer="(\d+)"', sheet) == [
        str(pack["lion"].pk),
        str(pack["tiger"].pk),
    ]


def test_a_forged_assign_from_the_wrong_subgroup_is_refused(coordinator, event, pack):
    lions = award(
        event, "Lion Paint", group=pack["group"], subgroup=subgroup(event, "Lions")
    )
    response = post(
        coordinator, f"/judging/award/{lions.pk}/assign/", {"racer": pack["tiger"].pk}
    )
    assert response.status_code == 422
    assert "For Lions only" in html(response)
    assert Award.objects.get(pk=lions.pk).racer_id is None


def test_assign_remove_and_undo(coordinator, event, pack):
    paint = award(event)
    response = post(
        coordinator,
        f"/judging/award/{paint.pk}/assign/",
        {"racer": pack["lion"].pk},
        target="judging-sheet-body",
    )
    assert json.loads(response["HX-Trigger"]) == {"judging-refresh": True}
    assert Award.objects.get(pk=paint.pk).racer_id == pack["lion"].pk
    removed = html(post(coordinator, f"/judging/award/{paint.pk}/remove/"))
    undo = re.search(r'hx-post="([^"]+restore[^"]+)"', removed).group(1)
    assert Award.objects.get(pk=paint.pk).racer_id is None
    undo = undo.replace("&amp;", "&")
    assert post(coordinator, undo).status_code == 200
    assert post(coordinator, undo).status_code == 200
    assert Award.objects.get(pk=paint.pk).racer_id == pack["lion"].pk


def test_an_undo_naming_a_racer_who_cannot_win_is_refused(coordinator, event, pack):
    lions = award(
        event, "Lion Paint", group=pack["group"], subgroup=subgroup(event, "Lions")
    )
    forged = f"/judging/award/{lions.pk}/restore/?racer={pack['tiger'].pk}"
    assert post(coordinator, forged).status_code == 422
    assert Award.objects.get(pk=lions.pk).racer_id is None


def test_the_ad_hoc_field(coordinator, event, pack):
    url = f"/judging/racer/{pack['lion'].pk}/adhoc/"
    post(coordinator, url, {"awardname": "Most Creative"}, target="judging-sheet-body")
    assert Award.objects.get(award_type_id=AD_HOC).name == "Most Creative"
    sheet = html(get(coordinator, f"/judging/racer/{pack['lion'].pk}/"))
    assert 'value="Most Creative"' in sheet
    post(coordinator, url, {"awardname": ""})
    assert not Award.objects.filter(award_type_id=AD_HOC).exists()


# ----- the presentation console -----


def test_the_no_kiosk_warning_shows_until_a_kiosk_shows_awards(coordinator, event):
    assert "data-no-kiosk" in html(coordinator.get("/awards/present/"))
    Kiosk.objects.create(event=event, address="tv", page="kiosks/slideshow.kiosk")
    assert "data-no-kiosk" in html(coordinator.get("/awards/present/"))
    Kiosk.objects.filter(address="tv").update(page="kiosks/award-presentations.kiosk")
    page = html(coordinator.get("/awards/present/"))
    assert "data-no-kiosk" not in page
    assert "alert(" not in page


def test_the_current_poll_shows_a_renamed_winner(coordinator, event, pack):
    """The signature covers every field the award shows, not only its key,
    reveal flag and winner (#45 retro)."""
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{paint.pk}")
    shown = html(get(coordinator, "/awards/present/current/"))
    sig = re.search(r'name="sig" value="([^"]+)"', shown).group(1)
    assert get(coordinator, "/awards/present/current/", {"sig": sig}).status_code == 204

    Racer.objects.filter(pk=pack["lion"].pk).update(first_name="Leo")

    renamed = get(coordinator, "/awards/present/current/", {"sig": sig})
    assert renamed.status_code == 200
    assert "Leo Racer" in html(renamed)


def test_a_forged_key_is_refused(coordinator, event):
    response = post(coordinator, "/awards/present/award/", {"key": "award-999999"})
    assert response.status_code == 422
    assert RaceState.objects.get(event=event).current_award_key == ""


def test_reveal_takes_only_on_or_off(coordinator, event):
    assert (
        post(coordinator, "/awards/present/reveal/", {"reveal": "yes"}).status_code
        == 422
    )


def current(client) -> dict:
    """`query=award.current`, with PHP's duration masked."""
    found = json.loads(client.get("/action.php", {"query": "award.current"}).content)
    if "current" in found:
        found["current"]["duration"] = "<seconds>"
    return found


def golden(exchanges, name) -> dict:
    return json.loads(exchanges[name]["body"])


def test_next_reveal_and_clear_change_award_current_as_php_does(client, event):
    """The PHP console posts `award.present` with a key and `reveal=0` for an
    award, `reveal` alone for the switch, and an empty key with `reveal=0`
    for Clear. After each console step here, `award.current` equals the
    reply PHP's capture recorded for that award (`awards.http`), with the
    reveal flag the step set."""
    from legacy_api.tests.test_awards_parity import AWARDS, run_awards

    run_awards(client)
    php = read_transcript(GOLDEN_DIR / AWARDS)

    assert post(client, "/awards/present/clear/").status_code == 200
    assert current(client) == golden(php, "033 current no winner") == {}

    assert (
        post(client, "/awards/present/next/", {"type": "", "scope": "all"}).status_code
        == 200
    )
    assert current(client) == golden(php, "034 current speed-1")

    assert (
        post(client, "/awards/present/next/", {"type": "", "scope": "all"}).status_code
        == 200
    )
    speed_2 = golden(php, "035 current speed-2")
    assert current(client) == speed_2

    assert post(client, "/awards/present/reveal/", {"reveal": "1"}).status_code == 200
    speed_2["current"]["reveal"] = True
    assert current(client) == speed_2

    paint = Award.objects.get(event=event, name="Best Paint Job")
    assert (
        post(client, "/awards/present/award/", {"key": f"award-{paint.pk}"}).status_code
        == 200
    )
    judged = golden(php, "031 current judged")
    judged["current"]["key"] = f"award-{paint.pk}"
    assert current(client) == judged

    assert post(client, "/awards/present/clear/").status_code == 200
    assert current(client) == {}
    assert RaceState.objects.get(event=event).reveal_current_award is False


def test_a_racer_card_shows_the_car_photo(coordinator, event, pack):
    Racer.objects.filter(pk=pack["lion"].pk).update(car_photo="lion.jpg")
    page = html(coordinator.get("/judging/"))
    card = re.search(rf'data-racer="{pack["lion"].pk}".*?</li>', page, re.S).group(0)
    assert re.search(r'<img src="/photo\.php/car/file/200x200/lion\.jpg/\d+"', card)


# ----- review of #45 -----


def test_the_current_poll_shows_a_new_winner_of_the_award_presented(
    coordinator, event, pack
):
    """PHP redraws award.current every second, so a judge reassigning the
    award being presented shows before the presenter taps Reveal."""
    paint = award(event)
    editing.assign_award(event, paint.pk, pack["lion"].pk)
    RaceState.objects.filter(event=event).update(current_award_key=f"award-{paint.pk}")
    shown = html(get(coordinator, "/awards/present/current/"))
    sig = re.search(r'name="sig" value="([^"]+)"', shown).group(1)
    editing.remove_winner(event, paint.pk)
    editing.assign_award(event, paint.pk, pack["tiger"].pk)
    again = get(coordinator, "/awards/present/current/", {"sig": sig})
    assert again.status_code == 200
    assert "Tiger Racer" in html(again)


def test_the_current_poll_waits_while_a_field_has_focus(coordinator, event):
    page = html(coordinator.get("/awards/present/"))
    poll = re.search(r'<div id="presenter-current"[^>]*>', page).group(0)
    assert "derbynet.pollable(this)" in poll


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "zzz", "scope": "all"},
        {"type": "99", "scope": "all"},
        {"type": "", "scope": "zzz"},
        {"type": "", "scope": "g999999"},
    ],
)
def test_next_refuses_a_filter_the_page_does_not_offer(
    coordinator, event, pack, fields
):
    award(event)
    response = post(coordinator, "/awards/present/next/", fields)
    assert response.status_code == 422
    assert 'data-toast="error"' in html(response)
    assert RaceState.objects.get(event=event).current_award_key == ""
