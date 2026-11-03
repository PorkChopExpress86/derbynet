"""The kiosk dashboard and the scene editor (#46)."""

import re
from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import Perm
from awards.models import Award
from core.models import RaceState
from kiosks import services
from kiosks.models import Kiosk, Scene, SceneKiosk
from roster.models import Group
from ui.kiosks import state

pytestmark = pytest.mark.django_db

SIG = re.compile(r'name="sig" value="([^"]+)"')


def kiosk(event, address, **fields) -> Kiosk:
    return Kiosk.objects.create(
        event=event, address=address, last_contact=timezone.now(), **fields
    )


def scene(event, name) -> Scene:
    return Scene.objects.get(event=event, name=name)


def board(client, sig=None):
    params = {"sig": sig} if sig else {}
    return client.get("/kiosks/board/", params, HTTP_HX_REQUEST="true")


def pressed(html: str) -> list[str]:
    return re.findall(r'aria-pressed="true" data-scene="(\d+)"', html)


@pytest.fixture
def coordinator(login):
    return login("RaceCoordinator")


@pytest.fixture
def presenter(event):
    """A role with PRESENT_AWARDS and not CONTROL_RACE (C37)."""
    role = Role(name="Presenter", permissions=int(Perm.PRESENT_AWARDS), position=9)
    role.set_password("present")
    role.save()
    client = Client()
    response = client.post("/login/", {"name": "Presenter", "password": "present"})
    assert response.status_code == 302
    return client


# ----- the dashboard -----------------------------------------------------------------


def test_every_built_in_page_has_a_label_and_its_own_thumbnail():
    assert set(state.PAGE_LABELS) == set(services.BUILT_IN_PAGES)
    fallback = render_to_string("ui/kiosks/thumbnail.html", {"brief": "?"})
    drawn = {
        brief: render_to_string("ui/kiosks/thumbnail.html", {"brief": brief})
        for brief in services.BUILT_IN_PAGES
    }
    others = {b: svg for b, svg in drawn.items() if b != "identify"}
    assert all(svg != fallback for svg in others.values())
    assert len({svg.split("</rect>", 1)[-1] for svg in drawn.values()}) == len(drawn)


def test_the_board_lists_kiosks_heard_from_in_the_last_minute(coordinator, event):
    kiosk(event, "10.0.0.1", name="Main")
    quiet = kiosk(event, "10.0.0.2")
    Kiosk.objects.filter(pk=quiet.pk).update(
        last_contact=timezone.now() - timedelta(seconds=61)
    )
    html = board(coordinator).content.decode()
    assert 'data-kiosk="10.0.0.1"' in html
    assert "10.0.0.2" not in html
    assert Kiosk.objects.filter(pk=quiet.pk).exists()


def test_a_card_shows_the_names_contact_page_and_parameters(coordinator, event):
    lions = Group.objects.create(event=event, name="Lions", sort_order=1)
    kiosk(
        event,
        "10.0.0.7",
        name="Lobby",
        page="kiosks/please-check-in.kiosk",
        params=f'{{"classids":[{lions.pk}]}}',
    )
    html = board(coordinator).content.decode()
    assert ">Lobby<" in html
    assert services.madlib("10.0.0.7") in html
    assert 'data-page="kiosks/please-check-in.kiosk">Please check in' in html
    assert "data-summary>Lions<" in html
    assert re.search(r'data-last-contact="\d+"', html)


def test_the_named_warning_counts_the_named_kiosks(coordinator, event):
    kiosk(event, "a", name="Main")
    kiosk(event, "b")
    html = board(coordinator).content.decode()
    assert "1 named kiosk out of 2" in html
    Kiosk.objects.filter(address="b").update(name="Aux1")
    html = board(coordinator).content.decode()
    assert "data-named-warning" not in html
    assert "2 named kiosks" in html


def test_the_poll_answers_204_until_the_board_changes(coordinator, event):
    kiosk(event, "a", name="Main")
    sig = SIG.search(board(coordinator).content.decode()).group(1)
    assert board(coordinator, sig).status_code == 204
    Kiosk.objects.filter(address="a").update(reload_requested=True)
    changed = board(coordinator, sig)
    assert changed.status_code == 200
    assert "Reloading" in changed.content.decode()


def test_a_scene_switch_still_shows_after_three_polls(coordinator, event):
    """C19: PHP's dashboard reads `current-scene`, which nothing writes, and
    resets its select at the next poll."""
    kiosk(event, "a", name="Main")
    racing = scene(event, "Racing")
    reply = coordinator.post("/kiosks/scene/", {"scene": racing.pk})
    assert reply.status_code == 200
    assert pressed(reply.content.decode()) == [str(racing.pk)]
    for _ in range(3):
        assert pressed(board(coordinator).content.decode()) == [str(racing.pk)]
    assert Kiosk.objects.get(address="a").page == "kiosks/now-racing.kiosk"


def test_a_scene_that_is_gone_is_refused(coordinator, event):
    reply = coordinator.post("/kiosks/scene/", {"scene": 999999})
    assert reply.status_code == 422


def test_a_presenter_sees_the_kiosks_and_no_control(presenter, event):
    """C37: the page needs PRESENT_AWARDS and every control CONTROL_RACE, so
    a presenter gets the board with no control, where PHP showed controls
    that all failed."""
    kiosk(event, "a", name="Main")
    page = presenter.get("/kiosks/").content.decode()
    assert 'data-kiosk="a"' in page
    assert "data-view-only" in page
    assert "hx-post" not in page
    assert 'data-action="page"' not in page
    for url, data in (
        ("/kiosks/scene/", {"scene": scene(event, "Racing").pk}),
        ("/kiosks/page/", {"address": "a", "page": "kiosks/flag.kiosk"}),
        ("/kiosks/name/", {"address": "a", "name": "Aux1"}),
        ("/kiosks/reload/", {"address": "a"}),
        ("/kiosks/identify/", {"address": "a"}),
        ("/kiosks/configure/", {"address": "a", "page": "kiosks/flag.kiosk"}),
    ):
        assert presenter.post(url, data, HTTP_HX_REQUEST="true").status_code == 403, url
    sheet = presenter.get("/kiosks/page/", {"address": "a"}, HTTP_HX_REQUEST="true")
    assert sheet.status_code == 403
    assert Kiosk.objects.get(address="a").name == "Main"


def test_the_page_picker_names_and_identify(coordinator, event):
    kiosk(event, "tv 1")
    sheet = coordinator.get("/kiosks/page/", {"address": "tv 1"}).content.decode()
    assert sheet.count("data-page-choice=") == len(services.BUILT_IN_PAGES)
    coordinator.post(
        "/kiosks/page/",
        {"address": "tv 1", "page": "kiosks/welcome.kiosk"},
        HTTP_HX_TARGET="kiosks-sheet-body",
    )
    assert Kiosk.objects.get(address="tv 1").page == "kiosks/welcome.kiosk"
    coordinator.post("/kiosks/identify/", {"address": "tv 1"})
    assert Kiosk.objects.get(address="tv 1").page == services.IDENTIFY


def test_the_name_sheet_offers_the_scene_names(coordinator, event):
    kiosk(event, "a")
    sheet = coordinator.get("/kiosks/name/", {"address": "a"}).content.decode()
    assert re.findall(r'data-value="([^"]+)"', sheet) == ["Main", "Aux1", "Aux2"]
    coordinator.post("/kiosks/name/", {"address": "a", "name": "Aux2"})
    assert Kiosk.objects.get(address="a").name == "Aux2"


def test_a_sheet_for_a_kiosk_that_is_gone_is_404(coordinator, event):
    assert coordinator.get("/kiosks/page/", {"address": "gone"}).status_code == 404
    reply = coordinator.post("/kiosks/reload/", {"address": "gone"})
    assert reply.status_code == 422


def test_the_slideshow_configure_sheet_shows_what_was_saved(
    coordinator, event, settings, tmp_path
):
    """C20: PHP's dashboard reads `params`, a key that does not exist, so
    its Configure dialogs never show the stored values."""
    settings.CONFIG_DIR = tmp_path
    (tmp_path / "slides" / "pack-night").mkdir(parents=True)
    lions = Group.objects.create(event=event, name="Lions", sort_order=1)
    tigers = Group.objects.create(event=event, name="Tigers", sort_order=2)
    kiosk(event, "a", page="kiosks/slideshow.kiosk")
    reply = coordinator.post(
        "/kiosks/configure/",
        {
            "address": "a",
            "page": "kiosks/slideshow.kiosk",
            "title": "Welcome, Pack 1",
            "subdir": "pack-night",
            "classids": [str(tigers.pk)],
        },
        HTTP_HX_TARGET="kiosks-sheet-body",
    )
    assert reply.status_code == 200
    sheet = coordinator.get("/kiosks/configure/", {"address": "a"}).content.decode()
    assert 'value="Welcome, Pack 1"' in sheet
    assert '<option value="pack-night" selected>' in sheet
    assert f'value="{tigers.pk}" checked' in sheet
    assert f'value="{lions.pk}" checked' not in sheet


def test_configure_for_each_page_with_parameters(coordinator, event):
    kiosk(
        event, "a", page="kiosks/award-presentations.kiosk", params="{confetti:false}"
    )
    sheet = coordinator.get("/kiosks/configure/", {"address": "a"}).content.decode()
    assert 'name="confetti" value="1" data-autofocus' in sheet
    Kiosk.objects.filter(address="a").update(page="kiosks/qrcode.kiosk", params="")
    sheet = coordinator.get("/kiosks/configure/", {"address": "a"}).content.decode()
    assert 'value="http://testserver/vote.php"' in sheet
    Kiosk.objects.filter(address="a").update(page="kiosks/flag.kiosk")
    assert coordinator.get("/kiosks/configure/", {"address": "a"}).status_code == 404


# ----- the scene editor ---------------------------------------------------------------


def test_opening_the_scene_editor_does_not_change_the_award_on_the_kiosk(
    coordinator, event
):
    """`scenes.php`'s live previews loaded the award kiosk, which cleared the
    award being presented."""
    award = Award.objects.create(event=event, name="Best", award_type_id=1, sort=1)
    RaceState.objects.filter(event=event).update(
        current_award_key=f"award-{award.pk}", reveal_current_award=True
    )
    before = coordinator.get("/action.php", {"query": "award.current"}).json()
    for found in Scene.objects.filter(event=event):
        page = coordinator.get("/kiosks/scenes/", {"scene": found.pk})
        assert page.status_code == 200
        assert "<iframe" not in page.content.decode()
    after = coordinator.get("/action.php", {"query": "award.current"}).json()
    assert after == before
    assert RaceState.objects.get(event=event).current_award_key == f"award-{award.pk}"


def test_the_editor_lists_every_kiosk_name_with_the_scenes_page(coordinator, event):
    html = coordinator.get(
        "/kiosks/scenes/", {"scene": scene(event, "Wrap-Up").pk}
    ).content.decode()
    assert re.findall(r'data-entry="([^"]+)"', html) == ["Main", "Aux1", "Aux2"]
    assert 'data-page="kiosks/standings.kiosk">Standings' in html
    # An imported local page shows as the page a kiosk shows for it (D10).
    assert 'data-page="local/kiosks/feedback.kiosk">Identify' in html
    welcome = coordinator.get(
        "/kiosks/scenes/", {"scene": scene(event, "Welcome").pk}
    ).content.decode()
    assert welcome.count("No page in this scene") == 2


def test_new_scene_loads_the_page_at_it_keeping_back(coordinator, event):
    reply = coordinator.post(
        "/kiosks/scenes/new/", {"name": "Pack Night", "back": "setup.php"}
    )
    made = Scene.objects.get(event=event, name="Pack Night")
    assert reply["HX-Redirect"] == f"/kiosks/scenes/?scene={made.pk}&back=setup.php"
    again = coordinator.post("/kiosks/scenes/new/", {"name": "pack night"})
    assert "data-error" in again.content.decode()


def test_delete_scene(coordinator, event):
    awards = scene(event, "Awards")
    sheet = coordinator.get("/kiosks/scenes/delete/", {"scene": awards.pk})
    assert "Delete 1 scene" in sheet.content.decode()
    reply = coordinator.post("/kiosks/scenes/delete/", {"scene": awards.pk})
    assert reply["HX-Redirect"] == "/kiosks/scenes/"
    assert not Scene.objects.filter(pk=awards.pk).exists()
    assert (
        coordinator.post("/kiosks/scenes/delete/", {"scene": awards.pk}).status_code
        == 422
    )


def test_add_kiosk_name_and_set_page_and_parameters(coordinator, event):
    awards = scene(event, "Awards")
    reply = coordinator.post(
        "/kiosks/scenes/name/",
        {"scene": awards.pk, "name": "Lobby", "page": "kiosks/qrcode.kiosk"},
        HTTP_HX_TARGET="scenes-sheet-body",
    )
    assert 'data-entry="Lobby"' in reply.content.decode()
    coordinator.post(
        "/kiosks/scenes/configure/",
        {
            "scene": awards.pk,
            "name": "Lobby",
            "page": "kiosks/qrcode.kiosk",
            "title": "Vote",
            "content": "https://pack.example/vote",
        },
        HTTP_HX_TARGET="scenes-sheet-body",
    )
    row = SceneKiosk.objects.get(scene=awards, kiosk_name="Lobby")
    assert row.params == '{"title":"Vote","content":"https://pack.example/vote"}'
    sheet = coordinator.get(
        "/kiosks/scenes/configure/", {"scene": awards.pk, "name": "Lobby"}
    ).content.decode()
    assert 'value="https://pack.example/vote"' in sheet
    coordinator.post(
        "/kiosks/scenes/page/",
        {"scene": awards.pk, "name": "Lobby", "page": ""},
        HTTP_HX_TARGET="scenes-sheet-body",
    )
    assert not SceneKiosk.objects.filter(scene=awards, kiosk_name="Lobby").exists()


def test_the_editor_needs_set_up(login, event):
    assert login("RaceCrew").get("/kiosks/scenes/").status_code in (302, 403)


# ----- redirects and back -------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/kiosk-dashboard.php", "/kiosks/"),
        ("/scenes.php", "/kiosks/scenes/"),
        ("/scenes.php?back=setup.php", "/kiosks/scenes/?back=setup.php"),
        ("/derbynet/scenes.php?back=setup.php", "/kiosks/scenes/?back=setup.php"),
    ],
)
def test_the_php_pages_redirect_keeping_back(client, event, old, new):
    response = client.get(old)
    assert response.status_code == 301
    assert response["Location"] == new


@pytest.mark.parametrize(
    ("query", "back"),
    [
        ("", "/kiosks/"),
        ("?back=setup.php", "/setup/"),
        ("?back=kiosk-dashboard.php", "/kiosks/"),
        ("?back=index.php", "/"),
        ("?back=checkin.php", "/checkin/"),
        ("?back=coordinator.php", "/dashboard/"),
    ],
)
def test_the_editor_goes_back_to_the_calling_page(coordinator, event, query, back):
    """`make_banner()` (`templates/banner.inc`) shows a back button for these
    five pages, and `scenes.php` names kiosk-dashboard.php with no `back`."""
    html = coordinator.get(f"/kiosks/scenes/{query}").content.decode()
    assert f'<a href="{back}" aria-label="Back"' in html


@pytest.mark.parametrize(
    "back", ["https://evil.example/", "/groups/", "scenes.php", "racer-results.php"]
)
def test_any_other_back_shows_no_back_button(coordinator, event, back):
    """`make_banner()` draws no back button for a page off its list."""
    html = coordinator.get("/kiosks/scenes/", {"back": back}).content.decode()
    assert 'aria-label="Back"' not in html
