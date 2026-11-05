"""The kiosk page registry (#52): the 15 legacy page strings, the mapping
from a legacy `Kiosks.page` string, and the one `poll.kiosk` serializer."""

import logging

import pytest

from kiosks import pages

LEGACY_PAGES = {
    "kiosks/award-presentations.kiosk",
    "kiosks/derbynet.kiosk",
    "kiosks/flag.kiosk",
    "kiosks/identify.kiosk",
    "kiosks/now-racing.kiosk",
    "kiosks/now-racing-columnar.kiosk",
    "kiosks/now-racing-columnar-reversed.kiosk",
    "kiosks/ondeck.kiosk",
    "kiosks/please-check-in.kiosk",
    "kiosks/qrcode.kiosk",
    "kiosks/results-by-racer.kiosk",
    "kiosks/scale-MPH.kiosk",
    "kiosks/slideshow.kiosk",
    "kiosks/standings.kiosk",
    "kiosks/welcome.kiosk",
}


def test_the_registry_has_exactly_the_15_legacy_page_strings():
    assert set(pages.PAGES) == LEGACY_PAGES
    names = [p.name for p in pages.PAGES.values()]
    assert names == sorted(names)
    assert all(p.public for p in pages.PAGES.values())


def test_the_pages_with_parameters_name_the_configure_dialogs_fields():
    taking = {p.key: p.parameters for p in pages.PAGES.values() if p.parameters}
    assert taking == {
        "kiosks/award-presentations.kiosk": ("confetti",),
        "kiosks/please-check-in.kiosk": ("classids",),
        "kiosks/qrcode.kiosk": ("title", "content"),
        "kiosks/slideshow.kiosk": ("title", "subdir", "classids"),
        "kiosks/standings.kiosk": ("key",),
    }


ROUND_TRIPS = [
    "kiosks/welcome.kiosk",
    "!kiosks/standings.kiosk",
    'kiosks/please-check-in.kiosk#{"classids":[1,2]}',
    '!kiosks/please-check-in.kiosk#{"classids":[1,2]}',
    "kiosks/award-presentations.kiosk#{confetti:false}",
    'kiosks/qrcode.kiosk#{"title":"A # in it","content":"x#y"}',
]


@pytest.mark.parametrize("raw", ROUND_TRIPS)
def test_a_legacy_string_round_trips_through_the_mapping(raw):
    found = pages.from_legacy(raw)

    assert found.unknown == ""
    assert pages.legacy_string(found.page, found.params, found.reload) == raw


@pytest.mark.parametrize("raw", ROUND_TRIPS)
def test_the_serializer_gives_the_legacy_kiosk_setting(raw):
    found = pages.from_legacy(raw)
    _, _, params = raw.removeprefix("!").partition("#")

    setting = pages.setting(found.page, found.params, name="Main", reload=found.reload)

    assert setting == {
        "page": raw.removeprefix("!").partition("#")[0],
        "name": "Main",
        "reload": raw.startswith("!"),
        "parameters": params or "{}",
    }


def test_the_serializer_for_a_new_kiosk_has_no_name():
    assert pages.setting(pages.IDENTIFY, "", name=None, reload=True) == {
        "page": "kiosks/identify.kiosk",
        "parameters": "{}",
        "reload": False,
    }


@pytest.mark.parametrize(
    "raw",
    [
        "local/kiosks/feedback.kiosk",
        '!local/kiosks/feedback.kiosk#{"a":1}',
        "kiosks/../settings.php",
        "kiosks/x.kiosk",
        "",
    ],
)
def test_a_page_with_no_entry_maps_to_identify_with_a_warning(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="kiosks.pages"):
        found = pages.from_legacy(raw)

    page = raw.removeprefix("!").partition("#")[0]
    assert (found.page, found.params, found.unknown) == (pages.IDENTIFY, "", page)
    assert found.reload == raw.startswith("!")
    assert repr(page) in caplog.text


def test_page_for_falls_back_to_identify():
    assert pages.page_for("kiosks/flag.kiosk").template == "kiosk/pages/flag.html"
    assert pages.page_for("../settings.php").key == pages.IDENTIFY
