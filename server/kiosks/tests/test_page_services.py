"""The services the kiosk dashboard and scene editor call (#46). Each checks
again, under the lock, what the page showed."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import RaceState
from kiosks import services
from kiosks.models import Kiosk, Scene, SceneKiosk
from kiosks.parameters import Posted
from kiosks.services import KioskFailure

pytestmark = pytest.mark.django_db


def kiosk(event, address, **fields) -> Kiosk:
    return Kiosk.objects.create(
        event=event, address=address, last_contact=timezone.now(), **fields
    )


def scene(event, name) -> Scene:
    return Scene.objects.get(event=event, name=name)


def current_scene(event):
    return RaceState.objects.get(event=event).current_scene_id


def test_listed_kiosks_reads_only(event):
    kiosk(event, "a")
    old = kiosk(event, "old")
    Kiosk.objects.filter(pk=old.pk).update(
        last_contact=timezone.now() - timedelta(hours=2)
    )
    assert [k.address for k in services.listed_kiosks(event, timezone.now())] == ["a"]
    assert Kiosk.objects.filter(pk=old.pk).exists()


def test_show_scene_applies_it_and_refuses_a_scene_gone(event):
    kiosk(event, "a", name="Main")
    racing = scene(event, "Racing")
    services.show_scene(event, racing.pk)
    assert current_scene(event) == racing.pk
    assert Kiosk.objects.get(address="a").page == "kiosks/now-racing.kiosk"
    with pytest.raises(KioskFailure):
        services.show_scene(event, 999999)
    assert current_scene(event) == racing.pk


def test_a_page_by_hand_turns_the_scene_off_and_drops_the_parameters(event):
    kiosk(event, "a", page="kiosks/slideshow.kiosk", params='{"title":"x"}')
    services.show_scene(event, scene(event, "Welcome").pk)
    services.set_kiosk_page(event, "a", "kiosks/standings.kiosk")
    found = Kiosk.objects.get(address="a")
    assert (found.page, found.params) == ("kiosks/standings.kiosk", "")
    assert current_scene(event) is None


@pytest.mark.parametrize("page", ["local/kiosks/feedback.kiosk", "", "kiosks/x"])
def test_only_a_built_in_page_can_be_picked(event, page):
    kiosk(event, "a")
    with pytest.raises(KioskFailure):
        services.set_kiosk_page(event, "a", page)


def test_each_change_refuses_a_kiosk_that_is_gone(event):
    for change in (
        lambda: services.set_kiosk_page(event, "gone", "kiosks/flag.kiosk"),
        lambda: services.identify_kiosk(event, "gone"),
        lambda: services.name_kiosk(event, "gone", "Main"),
        lambda: services.reload_kiosk(event, "gone"),
        lambda: services.configure_kiosk(
            event, "gone", "kiosks/award-presentations.kiosk", Posted()
        ),
    ):
        with pytest.raises(KioskFailure):
            change()


def test_naming_a_kiosk_takes_the_current_scenes_page(event):
    kiosk(event, "a")
    services.show_scene(event, scene(event, "Racing").pk)
    services.name_kiosk(event, "a", "  Aux1 ")
    found = Kiosk.objects.get(address="a")
    assert (found.name, found.page) == ("Aux1", "kiosks/ondeck.kiosk")


def test_identify_and_reload(event):
    kiosk(event, "a", page="kiosks/flag.kiosk")
    services.identify_kiosk(event, "a")
    services.reload_kiosk(event, "a")
    found = Kiosk.objects.get(address="a")
    assert (found.page, found.reload_requested) == (services.IDENTIFY, True)


def test_configure_refuses_a_kiosk_given_another_page(event):
    kiosk(event, "a", page="kiosks/award-presentations.kiosk")
    services.configure_kiosk(
        event, "a", "kiosks/award-presentations.kiosk", Posted(confetti=False)
    )
    assert Kiosk.objects.get(address="a").params == '{"confetti":false}'
    with pytest.raises(KioskFailure):
        services.configure_kiosk(event, "a", "kiosks/qrcode.kiosk", Posted())


def test_scene_kiosk_names_lists_main_first(event):
    assert services.scene_kiosk_names(event) == ["Main", "Aux1", "Aux2"]


def test_create_scene_refuses_blank_and_a_name_taken_in_any_case(event):
    made = services.create_scene(event, " Pack Night ")
    assert made.name == "Pack Night"
    for name in ("", "   ", "pack night", "x" * 201):
        with pytest.raises(KioskFailure):
            services.create_scene(event, name)


def test_remove_scene_twice_changes_nothing_the_second_time(event):
    awards = scene(event, "Awards")
    assert services.remove_scene(event, awards.pk) is True
    assert services.remove_scene(event, awards.pk) is False


def test_set_scene_page_keeps_parameters_for_the_same_page_only(event):
    awards = scene(event, "Awards")
    page = "kiosks/award-presentations.kiosk"
    services.configure_scene_kiosk(event, awards.pk, "Main", page, Posted())
    services.set_scene_page(event, awards.pk, "Main", page)
    row = SceneKiosk.objects.get(scene=awards, kiosk_name="Main")
    assert row.params == '{"confetti":true}'
    services.set_scene_page(event, awards.pk, "Main", "kiosks/flag.kiosk")
    row.refresh_from_db()
    assert (row.page, row.params) == ("kiosks/flag.kiosk", "")
    services.set_scene_page(event, awards.pk, "Main", "")
    assert not SceneKiosk.objects.filter(scene=awards, kiosk_name="Main").exists()


def test_a_new_kiosk_name_must_be_new(event):
    awards = scene(event, "Awards")
    with pytest.raises(KioskFailure):
        services.set_scene_page(
            event, awards.pk, "aux1", "kiosks/flag.kiosk", new_name=True
        )
    services.set_scene_page(
        event, awards.pk, "Lobby", "kiosks/flag.kiosk", new_name=True
    )
    assert "Lobby" in services.scene_kiosk_names(event)


def test_scene_changes_refuse_a_scene_gone_or_a_blank_name(event):
    with pytest.raises(KioskFailure):
        services.set_scene_page(event, 999999, "Main", "kiosks/flag.kiosk")
    with pytest.raises(KioskFailure):
        services.set_scene_page(event, scene(event, "Awards").pk, " ", "")
    with pytest.raises(KioskFailure):
        services.configure_scene_kiosk(
            event, scene(event, "Welcome").pk, "Main", "kiosks/qrcode.kiosk", Posted()
        )
