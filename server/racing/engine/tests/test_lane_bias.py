"""`lane_bias.py` against `tests/golden/engine/lane-bias.json` and
`critical-f-value.json`, every case, with the Q9 deviations of D25.

A lane-bias record is compared on `result` alone, field by field with the
same floats and keys. Its other fields are PHP's page text and PHP errors:
`sentences` and `rows_html` are what `history.php` prints (#49), `echoed` is
a debugging line, and `warnings`, `rows_warnings` and `rows_error` are PHP
notices. A critical-f-value record is compared on `value`, without PHP's
`warnings`.

Two Fix quirks of #20 make Django differ. Q9 changes every critical value.
Q24 changes the F statistic: PHP's sums of squares count the second-to-last
lane twice and leave out the last lane, and Django counts every lane once.
Each case where Django differs from PHP is listed once in
`tests/golden/deviations.json`: under Q24 when the F statistic differs,
else under Q9, with Django's records under `tests/golden/deviations/Q9/`
and `Q24/`. To write them again after a change, run
`uv run python -m racing.engine.tests.test_lane_bias` from `server/`.
"""

import json
import re

import pytest

from racing.engine.standings.lane_bias import (
    LaneStats,
    critical_f_value,
    lane_bias_analysis,
)

from .golden import DEVIATIONS_FILE, GOLDEN_DIR, canonical, engine_deviations

LANE_BIAS = "engine/lane-bias.json"
CRITICAL_F_VALUE = "engine/critical-f-value.json"
REASON = {
    ("Q9", LANE_BIAS): (
        "Django compares F with the critical value for df1; PHP uses df1 + 1."
    ),
    ("Q9", CRITICAL_F_VALUE): (
        "Django reads table column df1 - 1; PHP reads column df1."
    ),
    ("Q24", LANE_BIAS): (
        "Django's sums of squares count every lane once; PHP counts the "
        "second-to-last lane twice and leaves out the last. The critical "
        "value is Q9's."
    ),
}


def quirk_of(fixture, django, php):
    """The quirk a differing case is listed under."""
    if fixture == LANE_BIAS and (django["result"] or {}).get("f-statistic") != (
        php["result"] or {}
    ).get("f-statistic"):
        return "Q24"
    return "Q9"


def lane_times(kind, nlanes, lane, j):
    """`lane_times()` of `capture-engine.php`, the j-th time of a lane, as
    the capture stored it."""
    noise = ((j * 37 + lane * 11 + nlanes * 5) % 97) / 1000.0
    match kind:
        case "even":
            time = 3.0 + noise
        case "biased":
            time = 3.0 + 0.12 * (lane - 1) + noise
        case "one-slow":
            time = 3.0 + (0.2 if lane == 1 else 0.0) + noise
        case "identical":
            time = 3.25
        case "with-dnf":
            time = 9.9999 if j % 5 == 4 else 3.0 + 0.05 * (lane - 1) + noise
    # PDO binds the time as a string, which PHP writes with its `precision`
    # of 14 significant digits, so SQLite holds the time rounded to those.
    return float(f"{time:.14G}")


def lane_bias_record(case_id):
    nlanes, kind, runs = re.fullmatch(
        r"lanes=(\d+) kind=(\S+) runs=(\d+)", case_id
    ).groups()
    nlanes, runs = int(nlanes), int(runs)
    bias = lane_bias_analysis(
        [
            [lane_times(kind, nlanes, lane, j) for j in range(runs)]
            for lane in range(1, nlanes + 1)
        ]
    )
    if bias is None:
        return {"result": []}
    return {
        "result": {
            "biased": bias.biased,
            "f-statistic": bias.f_statistic,
            "df1": bias.df1,
            "df2": bias.df2,
            "critical-value": bias.critical_value,
        }
    }


def critical_f_value_record(case_id):
    df1, df2 = re.fullmatch(r"critical_f_value\((\d+), (\d+)\)", case_id).groups()
    return {"value": critical_f_value(int(df1), int(df2))}


DJANGO_RECORD = {LANE_BIAS: lane_bias_record, CRITICAL_F_VALUE: critical_f_value_record}


def php_records(fixture):
    records = json.loads((GOLDEN_DIR / fixture).read_text())
    fields = {LANE_BIAS: ("result",), CRITICAL_F_VALUE: ("value",)}[fixture]
    return {
        case_id: {key: record[key] for key in fields}
        for case_id, record in records.items()
    }


@pytest.mark.parametrize(
    ("fixture", "ncases"), [(LANE_BIAS, 140), (CRITICAL_F_VALUE, 1242)]
)
def test_every_case_follows_php_or_its_q9_deviation(fixture, ncases):
    php = php_records(fixture)
    deviated = engine_deviations(fixture)
    assert len(php) == ncases
    assert set(deviated) <= set(php)
    wrong = {}
    for case_id, php_record in php.items():
        django = canonical(DJANGO_RECORD[fixture](case_id))
        if case_id in deviated:
            if django == canonical(php_record):
                wrong[case_id] = "no longer differs from PHP; remove its deviation"
            elif django != canonical(deviated[case_id]):
                wrong[case_id] = f"{django} is not the deviation's record"
        elif django != canonical(php_record):
            wrong[case_id] = f"{django} differs from PHP's {canonical(php_record)}"
    assert wrong == {}


def test_critical_values_come_from_the_df1_column():
    assert critical_f_value(1, 1) == 39.8635
    assert critical_f_value(7, 3) == 5.2662
    assert critical_f_value(3, 85) == 2.1774
    assert critical_f_value(2, 35) == 2.4887
    assert critical_f_value(7, 10**8) == 1.7167
    assert critical_f_value(0, 10) is None
    assert critical_f_value(8, 10) is None
    assert critical_f_value(1, 0) is None


def test_with_no_critical_value_any_nonzero_f_is_biased():
    nine_lanes = [[3.0 + 0.01 * lane, 3.05 + 0.01 * lane] for lane in range(9)]
    bias = lane_bias_analysis(nine_lanes)
    assert (bias.df1, bias.critical_value) == (8, None)
    assert bias.f_statistic > 0 and bias.biased

    bias = lane_bias_analysis([[3.0, 3.5]] * 9)
    assert (bias.critical_value, bias.f_statistic, bias.biased) == (None, 0.0, False)


def test_lane_stats_count_dnfs_as_runs_and_leave_them_out_of_the_mean():
    bias = lane_bias_analysis([[3.0, 3.2], [3.1, 9.9, 3.3], [3.4, 10.5]])
    assert bias.lanes[0] == LaneStats(
        runs=2, dnfs=0, mean=pytest.approx(3.1), sample_variance=pytest.approx(0.02)
    )
    assert bias.lanes[1] == LaneStats(
        runs=3, dnfs=1, mean=pytest.approx(3.2), sample_variance=pytest.approx(0.02)
    )
    assert bias.lanes[2] == LaneStats(runs=2, dnfs=1, mean=3.4, sample_variance=None)


def test_too_little_evidence_has_no_result():
    assert lane_bias_analysis([]) is None
    assert lane_bias_analysis([[3.0, 3.1, 3.2]]) is None
    assert lane_bias_analysis([[9.9], [10.0]]) is None
    assert lane_bias_analysis([[3.0], [3.1]]) is None
    assert lane_bias_analysis([[3.0, 3.0], [3.1, 3.1]]) is None


def test_the_f_statistic_counts_every_lane_once():
    """A one-way ANOVA worked by hand: lane means 3.2, 3.3 and 3.6 over three
    runs each, so the sums of squares are 0.26 between lanes and 0.06
    within, and F = (0.26 / 2) / (0.06 / 6) = 13."""
    lanes = [[3.1, 3.2, 3.3], [3.2, 3.3, 3.4], [3.5, 3.6, 3.7]]
    bias = lane_bias_analysis(lanes)
    assert bias.f_statistic == pytest.approx(13.0)
    assert lane_bias_analysis(lanes[::-1]).f_statistic == pytest.approx(13.0)


def write_lane_bias_deviations():
    listing = json.loads(DEVIATIONS_FILE.read_text())
    others = [
        entry
        for entry in listing["deviations"]
        if not (entry["quirk"] in ("Q9", "Q24") and entry["fixture"] in DJANGO_RECORD)
    ]
    entries = []
    for (quirk, fixture), reason in REASON.items():
        differing = {}
        for case_id, php_record in php_records(fixture).items():
            django = DJANGO_RECORD[fixture](case_id)
            if canonical(django) != canonical(php_record) and (
                quirk_of(fixture, django, php_record) == quirk
            ):
                differing[case_id] = django
        expected = f"deviations/{quirk}/{fixture.removeprefix('engine/')}"
        lines = [f"{canonical(k)}: {canonical(v)}" for k, v in differing.items()]
        path = GOLDEN_DIR / expected
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{\n" + ",\n".join(lines) + "\n}\n")
        entries.append(
            {
                "quirk": quirk,
                "fixture": fixture,
                "cases": list(differing),
                "reason": reason,
                "expected": expected,
            }
        )
        print(f"{quirk} {fixture}: {len(differing)} cases, written to {expected}")
    listing["deviations"] = sorted(others + entries, key=lambda e: int(e["quirk"][1:]))
    DEVIATIONS_FILE.write_text(json.dumps(listing, indent=2) + "\n")


if __name__ == "__main__":
    write_lane_bias_deviations()
