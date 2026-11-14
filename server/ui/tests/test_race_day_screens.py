"""`tools/race_day_screens.py` (#61, D91): the screens it shoots are the
images the race-day guides embed.

A later ticket that changes a screen shoots it again with the script (D91),
so every picture in running-a-race, timer-operation, photo-upload and
balloting must be one the script takes, or that picture never gets taken
again. And a shot no guide embeds is a file the docs carry for nothing. The
script checks both at the end of its run (`unmatched()`); this checks them
from the source, with no server.
"""

import re
from pathlib import Path

from tools import race_day_screens as tool

SCRIPT = Path(tool.__file__).read_text()
# Every call of `s.shot(tab, "<guide>", "<name>")`.
SHOT = re.compile(r's\.shot\(\s*\w+,\s*"([\w-]+)",\s*"([\w-]+)"')


def shots_in_source() -> list[str]:
    return [f"{guide}/{name}.png" for guide, name in SHOT.findall(SCRIPT)]


def test_the_script_shoots_each_image_the_guides_embed_and_no_other():
    shots = shots_in_source()
    assert len(shots) == len(set(shots)), "two shots write one file"
    assert set(shots) == tool.embedded()


def test_every_shot_call_names_its_guide_and_file_as_literals():
    """The check above reads each call's literals. A shot named from a
    variable would slip past it."""
    calls = len(re.findall(r"\bs\.shot\(", SCRIPT))
    assert calls == len(shots_in_source())


def test_each_guide_folder_holds_only_the_images_its_guide_embeds():
    docs = tool.REPO / "docs" / "img"
    on_disk = {
        f"{guide}/{path.name}"
        for guide in tool.GUIDES
        for path in (docs / guide).glob("*")
    }
    assert on_disk == tool.embedded()


def test_unmatched_names_a_stray_shot_a_missing_shot_and_a_leftover_file(
    tmp_path,
):
    used = sorted(tool.embedded())
    (tmp_path / "balloting").mkdir()
    (tmp_path / "balloting" / "old.png").write_bytes(b"")
    shots = [*used[1:], "balloting/extra.png"]
    for name in shots:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(b"")
    assert tool.unmatched(shots, tmp_path) == [
        "shot, but no guide embeds it: balloting/extra.png",
        f"a guide embeds it, but it was not shot: {used[0]}",
        "left over from an earlier run: balloting/old.png",
    ]
    assert tool.unmatched(used, tmp_path / "none") == []


def test_a_lower_car_is_faster_in_every_lane():
    cars = [n for n in range(101, 111)] + [n for n in range(201, 208)]
    for lane in (1, 2, 3, 4):
        times = [float(tool.car_time(car, lane)) for car in cars]
        assert times == sorted(times) and len(set(times)) == len(cars)
