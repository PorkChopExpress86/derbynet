"""The kiosk dashboard and the scene editor (#46)."""

import re
from datetime import timedelta
from html import unescape

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import Perm
from awards.editing import set_winner
from awards.models import Award
from core.models import RaceState
from kiosks import services
from kiosks.madlib import madlib
from kiosks.models import Kiosk, Scene, SceneKiosk
from kiosks.pages import PAGES
from racing.services import polls
from roster.models import Group, Racer
from ui.kiosks import state
from ui.tests.race import race_setup, scheduled

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


FRAME = re.compile(r'<iframe srcdoc="([^"]*)" sandbox="([^"]*)"')
CARD = re.compile(r'<li data-kiosk="([^"]+)"')


def thumbnails(html: str) -> list[dict]:
    """Each thumbnail frame's sandbox and the page it draws."""
    return [
        {"html": unescape(doc), "sandbox": sandbox}
        for doc, sandbox in FRAME.findall(html)
    ]


def card_thumbnails(html: str) -> list[tuple[str, dict]]:
    """Each board card's address and its one thumbnail."""
    found = []
    parts = CARD.split(html)
    for address, rest in zip(parts[1::2], parts[2::2], strict=True):
        frames = thumbnails(rest)
        assert len(frames) == 1, address
        found.append((address, frames[0]))
    return found


def shown_page(html: str) -> str:
    return re.search(r'<body class="kiosk-page" data-page="([^"]+)"', html).group(1)


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


def test_every_built_in_page_has_a_label():
    assert set(state.PAGE_LABELS) == {p.name for p in PAGES.values()}


# ----- thumbnails (#237) -------------------------------------------------------------


def test_each_card_draws_its_kiosks_page_in_a_frame_that_runs_no_script(
    coordinator, event
):
    for i, key in enumerate(PAGES):
        kiosk(event, f"tv{i:02d}", page=key, name=f"TV {i}")
    html = board(coordinator).content.decode()

    drawn = {address: frame for address, frame in card_thumbnails(html)}
    assert len(drawn) == len(PAGES)
    for i, key in enumerate(PAGES):
        frame = drawn[f"tv{i:02d}"]
        assert frame["sandbox"] == "allow-same-origin"
        assert shown_page(frame["html"]) == key
        assert "data-thumbnail" in frame["html"]
        assert "<script" not in frame["html"]
        assert "hx-" not in frame["html"]


def test_a_thumbnail_draws_with_the_kiosks_parameters_name_and_address(
    coordinator, event
):
    race_setup(event, groups=(("Lions", 2), ("Tigers", 2)))
    Racer.objects.filter(event=event, car_number__in=[101, 201]).update(
        passed_inspection=False
    )
    tigers = Group.objects.get(event=event, name="Tigers")
    kiosk(
        event,
        "check-in",
        page="kiosks/please-check-in.kiosk",
        params=f'{{"classids":[{tigers.pk}]}}',
    )
    kiosk(event, "qr", page="kiosks/qrcode.kiosk", params='{"title":"Vote here"}')
    kiosk(event, "who", page="kiosks/identify.kiosk", name="Gym TV")
    drawn = dict(card_thumbnails(board(coordinator).content.decode()))

    assert re.findall(r"<h2>(\w+)</h2>", drawn["check-in"]["html"]) == ["Tigers"]
    assert ">Vote here</h2>" in drawn["qr"]["html"]
    assert ">Gym TV</p>" in drawn["who"]["html"]
    assert madlib("who") in drawn["who"]["html"]


RACING_PAGES = (
    "kiosks/now-racing.kiosk",
    "kiosks/now-racing-columnar.kiosk",
    "kiosks/now-racing-columnar-reversed.kiosk",
)


def test_d84_a_racing_thumbnail_shows_the_heat_being_raced(coordinator, event):
    """D84: the lineup of `current_racers()`, which the kiosk's first poll
    answers, by lane, in each now-racing layout."""
    round = race_setup(event, groups=(("Lions", 4),))["Lions"]
    scheduled(round)
    for i, key in enumerate(RACING_PAGES):
        kiosk(event, f"tv{i}", page=key)
    run = polls.running(event)
    lineup = polls.current_racers(run, run.settings.name_style)
    assert len(lineup) == 4
    drawn = dict(card_thumbnails(board(coordinator).content.decode()))

    for i, key in enumerate(RACING_PAGES):
        html = drawn[f"tv{i}"]["html"]
        for racer in lineup:
            lane = re.search(
                rf'data-lane="{racer["lane"]}".*?</div>\s*</div>', html, re.S
            ).group(0)
            assert f">{racer['carnumber']}<" in lane, (key, racer)
            assert f">{racer['name']}<" in lane, (key, racer)


def test_d84_an_award_thumbnail_shows_the_award_being_presented(coordinator, event):
    """D84: the award of `query=award.current` (`ui.presenter.state`), and
    its recipient once revealed."""
    race_setup(event, groups=(("Lions", 2),))
    winner = Racer.objects.filter(event=event).order_by("pk").first()
    award = Award.objects.create(
        event=event, name="Best Paint", award_type_id=1, sort=1
    )
    set_winner(event, award, winner)
    RaceState.objects.filter(event=event).update(
        current_award_key=f"award-{award.pk}", reveal_current_award=False
    )
    kiosk(event, "tv", page="kiosks/award-presentations.kiosk")

    html = dict(card_thumbnails(board(coordinator).content.decode()))["tv"]["html"]
    assert ">Best Paint</p>" in html
    assert re.search(r"data-reveal[^>]* hidden", html)

    RaceState.objects.filter(event=event).update(reveal_current_award=True)
    html = dict(card_thumbnails(board(coordinator).content.decode()))["tv"]["html"]
    assert ">Best Paint</p>" in html
    assert not re.search(r"data-reveal[^>]* hidden", html)
    assert f">{winner.first_name} {winner.last_name}</p>" in html


def test_d84_no_award_shows_the_pages_own_title(coordinator, event):
    kiosk(event, "tv", page="kiosks/award-presentations.kiosk")
    html = dict(card_thumbnails(board(coordinator).content.decode()))["tv"]["html"]
    assert ">Award Presentation</p>" in html


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("kiosks/ondeck.kiosk", "The heat chart shows on the kiosk."),
        ("kiosks/qrcode.kiosk", "The QR code shows on the kiosk."),
    ],
)
def test_d84_ondeck_and_qr_thumbnails_say_what_shows_on_the_kiosk(
    coordinator, event, key, label
):
    kiosk(event, "tv", page=key)
    html = dict(card_thumbnails(board(coordinator).content.decode()))["tv"]["html"]
    assert f'<p class="k-thumb-note" data-thumb-note>{label}</p>' in html


def test_a_page_that_is_not_built_in_draws_identify(coordinator, event):
    kiosk(event, "tv", page="local/kiosks/mine.kiosk")
    drawn = dict(card_thumbnails(board(coordinator).content.decode()))

    assert shown_page(drawn["tv"]["html"]) == "kiosks/identify.kiosk"


def test_the_poll_answers_204_until_a_thumbnails_data_changes(coordinator, event):
    """The board's signature covers its thumbnails, so a racer checking in
    redraws the please-check-in thumbnail at the next poll."""
    race_setup(event, groups=(("Lions", 2),))
    Racer.objects.filter(event=event, car_number=101).update(passed_inspection=False)
    kiosk(event, "tv", page="kiosks/please-check-in.kiosk")
    sig = SIG.search(board(coordinator).content.decode()).group(1)
    assert board(coordinator, sig).status_code == 204

    Racer.objects.filter(event=event, car_number=101).update(passed_inspection=True)
    reply = board(coordinator, sig)

    assert reply.status_code == 200
    assert "data-all-in" in dict(card_thumbnails(reply.content.decode()))["tv"]["html"]


def test_the_page_pickers_draw_each_page(coordinator, event):
    kiosk(event, "tv", name="Gym TV")
    sheets = [
        coordinator.get("/kiosks/page/", {"address": "tv"}),
        coordinator.get(
            "/kiosks/scenes/page/",
            {"scene": scene(event, "Racing").pk, "name": "Main"},
        ),
    ]
    for sheet in sheets:
        assert sheet.status_code == 200
        frames = thumbnails(sheet.content.decode())
        assert [shown_page(f["html"]) for f in frames] == list(PAGES)
        assert all(f["sandbox"] == "allow-same-origin" for f in frames)
        assert all("<script" not in f["html"] for f in frames)


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
    assert "data-last-contact" not in html


def test_a_kiosk_checking_in_on_time_leaves_the_board_unchanged(coordinator, event):
    """A kiosk checks in every 5 s. The board changes only when one goes
    quiet, so the poll answers 204 between real changes and a tap never
    lands on a board being swapped (#46 CI)."""
    kiosk(event, "a", name="Main")
    Kiosk.objects.filter(address="a").update(
        last_contact=timezone.now() - timedelta(seconds=3)
    )
    sig = SIG.search(board(coordinator).content.decode()).group(1)
    Kiosk.objects.filter(address="a").update(last_contact=timezone.now())
    assert board(coordinator, sig).status_code == 204
    Kiosk.objects.filter(address="a").update(
        last_contact=timezone.now() - timedelta(seconds=20)
    )
    quiet = board(coordinator, sig)
    assert quiet.status_code == 200
    stamp = int((timezone.now() - timedelta(seconds=20)).timestamp())
    found = int(re.search(r'data-last-contact="(\d+)"', quiet.content.decode())[1])
    assert abs(found - stamp) <= 1


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


def test_preview_opens_the_kiosks_page_and_registers_nothing(presenter, event):
    """#46 left Preview out until #52's preview had no side effects."""
    import html as markup

    kiosk(
        event,
        "a",
        name="Main",
        page="kiosks/qrcode.kiosk",
        params='{"title":"Hello","content":"x"}',
    )
    page = presenter.get("/kiosks/").content.decode()
    link = re.search(r'<a href="([^"]+)"[^>]*data-action="preview"', page).group(1)
    before = list(Kiosk.objects.values_list("address", "last_contact"))

    shown = presenter.get(markup.unescape(link)).content.decode()

    assert 'data-page="kiosks/qrcode.kiosk"' in shown
    assert ">Hello<" in shown
    assert list(Kiosk.objects.values_list("address", "last_contact")) == before


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
    assert sheet.count("data-page-choice=") == len(PAGES)
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
        # A thumbnail is a frame that runs no script (#237).
        frames = thumbnails(page.content.decode())
        assert all(f["sandbox"] == "allow-same-origin" for f in frames)
        assert all("<script" not in f["html"] for f in frames)
        assert page.content.decode().count("<iframe") == len(frames)
    after = coordinator.get("/action.php", {"query": "award.current"}).json()
    assert after == before
    assert RaceState.objects.get(event=event).current_award_key == f"award-{award.pk}"


def test_the_editor_lists_every_kiosk_name_with_the_scenes_page(coordinator, event):
    # `scene.setkiosk` still stores any page string, as PHP's does.
    wrap_up = scene(event, "Wrap-Up")
    SceneKiosk.objects.create(
        event=event,
        scene=wrap_up,
        kiosk_name="Aux2",
        page="local/kiosks/feedback.kiosk",
    )
    html = coordinator.get(
        "/kiosks/scenes/", {"scene": scene(event, "Wrap-Up").pk}
    ).content.decode()
    assert re.findall(r'data-entry="([^"]+)"', html) == ["Main", "Aux1", "Aux2"]
    assert 'data-page="kiosks/standings.kiosk">Standings' in html
    # A page outside the registry shows as the page a kiosk shows for it (D10).
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
