"""Q3 of #20, fixed by #65: a bye in heat index 0 takes a displaced racer.

PHP's `squeeze_out_one_heat_of_byes()` tests `!$relos[$lane]` and
`$relos[$lane] != null` (`action.schedule.reschedule.inc:400` and `:413`),
which take heat index 0 for no heat, so a heat whose racers fit only a bye in
the round's first heat is never squeezed out.
"""

import json

from racing.engine.scheduling.adjuster import NodeBudget, ScheduleAdjuster
from racing.engine.scheduling.round import make_chart
from racing.engine.tests.golden import GOLDEN_DIR, read_transcript

FIRST_HEAT_BYE = "http/reschedule/first-heat-bye.http"


def squeeze(chart, nlanes, mask=0, departed=(), raced=()):
    adjuster = ScheduleAdjuster(
        chart, {h: h + 1 for h in raced}, mask, nlanes, 1, NodeBudget(1000)
    )
    adjuster.remove_departed(list(departed))
    adjuster.squeeze_out_byes()
    return adjuster


def test_the_first_heats_bye_takes_a_racer_of_the_heat_squeezed_out():
    """Heat 3's racers fit the byes of heats 1 and 2, and heat 1 is index 0.
    With a raced heat before them, the same byes sit at indices 1 and 2 and
    give the same chart."""
    adjuster = squeeze([[1, None], [None, 2], [3, 4]], 2)
    assert adjuster.chart_debug() == ["1 4", "3 2"]
    assert adjuster.heats_squeezed_out == 1

    adjuster = squeeze([[5, 6], [1, None], [None, 2], [3, 4]], 2, raced=[0])
    assert adjuster.chart_debug() == [None, "1 4", "3 2"]
    assert adjuster.heats_squeezed_out == 1


def test_a_racer_leaving_heat_1_lets_the_squeeze_remove_a_heat():
    """`capture/scenarios/11-reschedule-quirks.sh`'s Q3 case: five racers on
    lanes 1 and 2 of 4, no heat raced, racer 1 leaves. Its byes are in heat 1
    (index 0) and heat 3, and only the first takes heat 2's racer in lane 1.
    Django squeezes heat 2 out and leaves four heats. PHP's reply in the
    fixture keeps all five, with both byes."""
    chart = [heat + [None, None] for heat in make_chart([1, 2, 3, 4, 5], 2, 1)]
    assert [" ".join(str(r or "-") for r in heat) for heat in chart] == [
        "1 4 - -",
        "2 5 - -",
        "3 1 - -",
        "4 2 - -",
        "5 3 - -",
    ]
    adjuster = squeeze(chart, 4, mask=0b1100, departed=[1])
    assert adjuster.chart_debug() == ["2 4 - -", "3 5 - -", "4 2 - -", "5 3 - -"]
    assert adjuster.heats_squeezed_out == 1

    (php,) = read_transcript(GOLDEN_DIR / FIRST_HEAT_BYE).values()
    assert json.loads(php["body"])["chart"] == [
        "- 4 - -",
        "2 5 - -",
        "3 - - -",
        "4 2 - -",
        "5 3 - -",
    ]
