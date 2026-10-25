"""The schedulers against the PHP fixtures of #7, byte for byte.

`-m pr` runs on every pull request; `-m full` runs the whole ordered sweep in
the nightly `schedule-parity` workflow. A case is a canonical record hashed
the way `capture-engine.php` hashes it, so a match is byte for byte.
"""

import pytest

from racing.engine.scheduling.ordered import (
    make_ordered_schedule,
    reference_ordered_schedule,
)
from racing.engine.scheduling.rotation import make_rotation_schedule
from racing.engine.scheduling.rough import CantFulfill

from .golden import (
    GOLDEN_DIR,
    canonical,
    engine_deviations,
    ordered_manifest,
    read_cases,
    rotation_manifest,
    sha256,
)

ORDERED = ordered_manifest()
LANE_COUNTS = (1, 2, 3, 4, 5, 6, 8)
EXTENDED_CASE = "ordered/6/200/6/0"


def parse(case_id):
    return tuple(int(part) for part in case_id.split("/")[1:])


def ordered_record(scheduler, nlanes, ncars, n_times_per_lane, max_runs_per_car):
    try:
        heats = scheduler(nlanes, ncars, n_times_per_lane, max_runs_per_car)
    except CantFulfill:
        # PHP's json_failure('cant-fulfill') with the empty schedule it returns.
        return {"failure": "cant-fulfill", "nheats": 0, "heats": []}
    return {"failure": None, "nheats": len(heats), "heats": heats}


def rotation_record(nlanes, ncars, n_times_per_lane):
    heats = make_rotation_schedule(nlanes, ncars, n_times_per_lane)
    # PHP's json_out() keys, as the capture records them: nheats is the
    # heats per run.
    return {"failure": None, "nheats": ncars, "ncars": ncars, "heats": heats}


def mismatches(manifest, case_ids, make_record):
    return [
        case_id
        for case_id in case_ids
        if sha256(make_record(*parse(case_id))) != manifest[case_id]
    ]


def in_pr_grid(case_id):
    nlanes, ncars, n_times, max_runs = parse(case_id)
    return (ncars <= 60 and n_times <= 3) or max_runs == 1


def ordered_cases(nlanes, keep):
    return [c for c in ORDERED if parse(c)[0] == nlanes and keep(c)]


@pytest.mark.pr
def test_the_grids_cover_what_the_ticket_names():
    for nlanes in LANE_COUNTS:
        car_counts = {parse(c)[1] for c in ordered_cases(nlanes, in_pr_grid)}
        assert set(range(nlanes, 61)) <= car_counts
    assert EXTENDED_CASE in ORDERED
    assert len(ORDERED) == 12624
    assert len(rotation_manifest()) == 4116


@pytest.mark.pr
@pytest.mark.parametrize("nlanes", LANE_COUNTS)
def test_ordered_matches_php_to_60_cars(nlanes):
    cases = ordered_cases(nlanes, in_pr_grid)
    assert cases
    bad = mismatches(
        ORDERED, cases, lambda *a: ordered_record(make_ordered_schedule, *a)
    )
    assert bad == []


@pytest.mark.pr
@pytest.mark.parametrize("nlanes", LANE_COUNTS)
def test_reference_port_matches_php_to_60_cars(nlanes):
    cases = ordered_cases(nlanes, in_pr_grid)
    bad = mismatches(
        ORDERED, cases, lambda *a: ordered_record(reference_ordered_schedule, *a)
    )
    assert bad == []


@pytest.mark.pr
@pytest.mark.parametrize(
    "scheduler", [make_ordered_schedule, reference_ordered_schedule]
)
def test_200_cars_on_6_lanes_with_6_runs_matches_php(scheduler):
    record = ordered_record(scheduler, *parse(EXTENDED_CASE))
    assert record["nheats"] == 1200
    assert sha256(record) == ORDERED[EXTENDED_CASE]


@pytest.mark.pr
def test_rotation_matches_php():
    manifest = rotation_manifest()
    assert mismatches(manifest, manifest, rotation_record) == []


@pytest.mark.pr
def test_full_records_hash_to_their_manifest_lines():
    """The hash compare above is only as good as the record format."""
    for path in sorted((GOLDEN_DIR / "engine" / "ordered" / "full").glob("*.json")):
        nlanes, ncars = int(path.stem[1:3]), int(path.stem[5:])
        for key, record in read_cases(path).items():
            n_times, max_runs = (int(x) for x in key[1:].split("-m"))
            case_id = f"ordered/{nlanes}/{ncars}/{n_times}/{max_runs}"
            assert sha256(record) == ORDERED[case_id], case_id


@pytest.mark.pr
def test_out_of_range_follows_php_or_its_deviation():
    fixture = "engine/out-of-range.json"
    php = read_cases(GOLDEN_DIR / fixture)
    deviated = engine_deviations(fixture)
    assert set(deviated) <= set(php)
    for case_id, php_record in php.items():
        django = ordered_record(make_ordered_schedule, *parse(case_id), 0)
        if case_id in deviated:
            assert canonical(django) == canonical(deviated[case_id]), case_id
            assert canonical(django) != canonical(php_record), (
                f"{case_id} no longer differs from PHP; remove its deviation"
            )
        else:
            assert canonical(django) == canonical(php_record), case_id


@pytest.mark.full
@pytest.mark.parametrize("nlanes", LANE_COUNTS)
def test_ordered_matches_php_for_the_whole_sweep(nlanes):
    cases = ordered_cases(nlanes, lambda c: True)
    bad = mismatches(
        ORDERED, cases, lambda *a: ordered_record(make_ordered_schedule, *a)
    )
    assert bad == []
