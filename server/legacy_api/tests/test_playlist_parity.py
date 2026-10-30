"""`playlist.add` against the PHP capture (#34): the four steps
`testing/test-playlist.sh` adds, from `http/suite/playlist.http`, compared
byte for byte with ids normalized as `php_http` describes. The race the
script then runs is `racing/tests/test_playlist_script.py`, and the script
itself passes against a Django server."""

import pytest

from kiosks.models import Scene
from legacy_api.tests.test_race_control_parity import Scenario, _dump, active_event

pytestmark = pytest.mark.django_db

PLAYLIST = "http/suite/playlist.http"


def run_playlist(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    for car, den in (
        (101, "Den1"),
        (102, "Den1"),
        (201, "Den2"),
        (202, "Den2"),
        (203, "Den2"),
        (301, "Den3"),
        (302, "Den3"),
    ):
        s.import_racer(
            f"firstname=First{car}&lastname=Last{car}&partition={den}&carnumber={car}"
        )
    s.learn_rows()
    s.post_ok("action=class.add&name=TwoThree&constituent_2&constituent_3")
    s.learn_rows()
    s.post_ok("action=racer.bulk&who=all&what=checkin")
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=2")
    scenes = Scene.objects.filter(event=active_event()).order_by("sort_order")
    for n, scene in enumerate(scenes, start=1):
        s.ids.to_php["scene"][scene.pk] = n
    s.post_ok("action=schedule.generate&roundid=1")

    s.scenario(PLAYLIST)
    for _ in range(4):
        s.capture("playlist.add")
    return s


def test_the_playlist_steps_match_php(client, event):
    s = run_playlist(client)
    _dump(s)
    assert s.mismatches() == []
