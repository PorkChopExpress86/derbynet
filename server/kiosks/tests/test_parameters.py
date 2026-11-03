"""The Configure sheets' parameters (#46): encoded as `kiosk-parameters.js`
encodes them, and checked against the event."""

import json

import pytest

from kiosks import parameters
from kiosks.parameters import ParameterRefused, Posted
from roster.models import Group

pytestmark = pytest.mark.django_db


@pytest.fixture
def groups(event):
    return [
        Group.objects.create(event=event, name=name, sort_order=order)
        for order, name in ((2, "Tigers"), (1, "Lions"))
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"title":"Hi","classids":[1,2]}', {"title": "Hi", "classids": [1, 2]}),
        ("{confetti:false}", {"confetti": False}),
        ("", {}),
        ("{}", {}),
        ("[1,2]", {}),
        ("not json", {}),
    ],
)
def test_stored_reads_json_and_bare_keys(text, expected):
    assert parameters.stored(text) == expected


def test_please_check_in_takes_the_classes_in_class_order(event, groups):
    tigers, lions = groups
    text = parameters.encode(
        event,
        parameters.PLEASE_CHECK_IN,
        Posted(classids=(str(tigers.pk), str(lions.pk))),
    )
    assert text == f'{{"classids":[{lions.pk},{tigers.pk}]}}'


def test_slideshow_writes_title_subdir_and_classes_in_order(event, groups):
    text = parameters.encode(
        event,
        parameters.SLIDESHOW,
        Posted(title="Welcome, families", classids=(str(groups[0].pk),)),
    )
    assert text == (
        f'{{"title":"Welcome, families","subdir":"","classids":[{groups[0].pk}]}}'
    )


def test_a_slide_folder_must_exist(event, settings, tmp_path):
    settings.CONFIG_DIR = tmp_path
    (tmp_path / "slides" / "pack-night").mkdir(parents=True)
    (tmp_path / "slides" / ".hidden").mkdir()
    assert parameters.choices(event, parameters.SLIDESHOW).subdirs == ("pack-night",)
    text = parameters.encode(event, parameters.SLIDESHOW, Posted(subdir="pack-night"))
    assert json.loads(text)["subdir"] == "pack-night"
    with pytest.raises(ParameterRefused):
        parameters.encode(event, parameters.SLIDESHOW, Posted(subdir="../etc"))


@pytest.mark.parametrize("raw", ["999999", "x"])
def test_a_class_of_no_group_of_the_event_is_refused(event, groups, raw):
    with pytest.raises(ParameterRefused):
        parameters.encode(event, parameters.PLEASE_CHECK_IN, Posted(classids=(raw,)))


def test_standings_takes_a_key_of_the_catalog_only(event):
    with pytest.raises(ParameterRefused):
        parameters.encode(event, parameters.STANDINGS, Posted(key="forged"))


def test_award_presentations_and_qrcode(event):
    assert (
        parameters.encode(event, parameters.AWARDS, Posted(confetti=False))
        == '{"confetti":false}'
    )
    assert (
        parameters.encode(
            event, parameters.QRCODE, Posted(title="Vote", content="http://x/vote.php")
        )
        == '{"title":"Vote","content":"http://x/vote.php"}'
    )


def test_a_page_with_no_parameters_or_a_long_title_is_refused(event):
    with pytest.raises(ParameterRefused):
        parameters.encode(event, "kiosks/welcome.kiosk", Posted())
    with pytest.raises(ParameterRefused):
        parameters.encode(event, parameters.QRCODE, Posted(title="x" * 501))
