"""Every `schedule.reschedule` PHP recorded, replayed through the adjuster.

`reschedule_replay` rebuilds the chart each call started from. On PHP's
inputs the adjuster must give PHP's chart exactly, except where PHP answers
`nochart` for want of one more search (Q21). On Django's inputs, which also
keep the running heat and the staged heat (D34), it must give PHP's chart or
the chart a deviation entry names.

The capture tool leaves out any reschedule whose search could have reached
its deadline (`tests/golden/README.md`, "Reschedules"), and notes each one it
keeps as deterministic. PHP's `run-N-timed-out` flag is no test of that: it
is also set when a search stops on a perfect score. Exchanges with the flag
are marked in the test ids and compared exactly like the rest.
"""

import json

import pytest

from racing.engine.tests.golden import (
    GOLDEN_DIR,
    read_transcript,
    transcript_deviations,
)
from racing.engine.tests.reschedule_replay import (
    QUIRK_FIXTURES,
    SUITE_FIXTURE,
    php_changed_a_kept_heat,
    replay,
    replay_quirks,
)

FIXTURES = [SUITE_FIXTURE, *QUIRK_FIXTURES]


def php_charts():
    """`{(fixture, exchange): (chart or None for nochart, flagged)}`."""
    charts = {}
    for fixture in FIXTURES:
        for name, exchange in read_transcript(GOLDEN_DIR / fixture).items():
            if "action=schedule.reschedule" not in exchange.get("request-body", ""):
                continue
            assert exchange["note"].startswith("deterministic"), (fixture, name)
            body = json.loads(exchange["body"])
            success = body["outcome"]["code"] == "success"
            flagged = any(
                key.endswith("-timed-out")
                for key in body.get("timing", {}).get("internal", {})
            )
            charts[fixture, name] = (body["chart"] if success else None, flagged)
    return charts


PHP = php_charts()
IDS = [
    f"{fixture}:{name}{' (timed-out flag)' if flagged else ''}"
    for (fixture, name), (_, flagged) in PHP.items()
]


def replayed(keep_staged_heats):
    replies = [((SUITE_FIXTURE, name), r) for name, r in replay(keep_staged_heats)]
    replies += replay_quirks(keep_staged_heats)
    return dict(replies)


@pytest.fixture(scope="module")
def on_php_inputs():
    return replayed(False)


@pytest.fixture(scope="module")
def on_django_inputs():
    return replayed(True)


def expected_django_chart(fixture, name):
    _, deviation = transcript_deviations(fixture).get(name, (None, None))
    if deviation is None:
        return PHP[fixture, name][0]
    body = json.loads(deviation["body"])
    return body["chart"] if body["outcome"]["code"] == "success" else None


def charts(replies):
    return {key: reply.chart for key, reply in replies.items()}


def test_every_recorded_reschedule_is_replayed(on_php_inputs, on_django_inputs):
    assert list(charts(on_php_inputs)) == list(PHP)
    assert list(charts(on_django_inputs)) == list(PHP)


@pytest.mark.parametrize("key", list(PHP), ids=IDS)
def test_php_inputs_give_the_php_chart(on_php_inputs, key):
    quirk, _ = transcript_deviations(key[0]).get(key[1], (None, None))
    expected = expected_django_chart(*key) if quirk == "Q21" else PHP[key][0]
    assert on_php_inputs[key].chart == expected


@pytest.mark.parametrize("key", list(PHP), ids=IDS)
def test_django_inputs_give_the_php_chart_or_the_deviation(on_django_inputs, key):
    assert on_django_inputs[key].chart == expected_django_chart(*key)


def test_django_keeps_the_running_and_staged_heats(on_django_inputs):
    kept = 0
    for key, reply in on_django_inputs.items():
        for number, before in reply.kept.items():
            assert reply.chart[number - 1] == before, (key, number)
            kept += 1
    assert kept > 40


def test_each_q20_deviation_is_a_kept_heat_php_changed(on_django_inputs):
    """D34 explains a Q20 entry only where PHP changed the running heat or the
    staged heat, so any other difference from PHP needs its own quirk."""
    for fixture in FIXTURES:
        for name, (quirk, _) in transcript_deviations(fixture).items():
            if quirk == "Q20":
                reply = on_django_inputs[fixture, name]
                assert php_changed_a_kept_heat(PHP[fixture, name][0], reply.kept), name


def test_each_chart_deviation_still_differs_from_php():
    """Q20 and Q21 change the chart. Q5 changes only the racing state, which
    the service tests check."""
    for fixture in FIXTURES:
        for name, (quirk, _) in transcript_deviations(fixture).items():
            if quirk in ("Q20", "Q21"):
                assert expected_django_chart(fixture, name) != PHP[fixture, name][0]
