import json

from racing.engine.scheduling.lanes import (
    count_one_bits,
    mirror_lanes,
    spread_across_unused_lanes,
)

from .golden import GOLDEN_DIR

CASES = json.loads((GOLDEN_DIR / "engine" / "unused-lanes.json").read_text())


def test_every_mask_leaving_two_lanes_on_6_and_8_lanes_is_in_the_fixture():
    expected = {
        f"lanes={nlanes} mask={mask}"
        for nlanes in (6, 8)
        for mask in range(1 << nlanes)
        if nlanes - bin(mask).count("1") >= 2
    }
    assert set(CASES) == expected


def test_spread_matches_php_for_every_mask():
    wrong = []
    for name, case in CASES.items():
        mask = int(name.split("mask=")[1])
        assert "error" not in case and "warnings" not in case
        if count_one_bits(mask) != case["unused_lanes"] or (
            spread_across_unused_lanes(case["narrow"], mask) != case["wide"]
        ):
            wrong.append(name)
    assert wrong == []


def test_mirror_lanes_reverses_the_first_nlanes_values():
    assert mirror_lanes([1, 2, 3, 4], 4) == [4, 3, 2, 1]
    assert mirror_lanes(["a", "b", "c", "x"], 3) == ["c", "b", "a", "x"]
    assert mirror_lanes([], 0) == []
