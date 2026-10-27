"""Every `schedule.reschedule` PHP recorded, replayed through the adjuster.

`reschedule_replay` rebuilds the chart each call started from. Each reply is
compared in `django_reply` form: the chart and the `timing.internal` keys. On
PHP's inputs the adjuster must give PHP's reply exactly, except where PHP
answers `nochart` for want of one more search (Q21). On Django's inputs,
which also keep the running heat and the staged heat (D34), it must give
PHP's reply or the one a deviation entry records.

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
    django_reply,
    engine,
    php_changed_a_kept_heat,
    replay,
    replay_quirks,
)

FIXTURES = [SUITE_FIXTURE, *QUIRK_FIXTURES]
CHART_QUIRKS = ("Q20", "Q21", "Q25")


def php_replies():
    """`{(fixture, exchange): (django_reply form of PHP's reply, flagged)}`."""
    replies = {}
    for fixture in FIXTURES:
        for name, exchange in read_transcript(GOLDEN_DIR / fixture).items():
            if "action=schedule.reschedule" not in exchange.get("request-body", ""):
                continue
            assert exchange["note"].startswith("deterministic"), (fixture, name)
            body = json.loads(exchange["body"])
            internal = body.get("timing", {}).get("internal", {})
            flagged = any(key.endswith("-timed-out") for key in internal)
            success = body["outcome"]["code"] == "success"
            reply = django_reply(body["chart"], internal) if success else None
            replies[fixture, name] = (reply, flagged)
    return replies


PHP = php_replies()
IDS = [
    f"{fixture}:{name}{' (timed-out flag)' if flagged else ''}"
    for (fixture, name), (_, flagged) in PHP.items()
]


def replayed(new_server):
    replies = [((SUITE_FIXTURE, name), r) for name, r in replay(new_server)]
    replies += replay_quirks(new_server)
    return dict(replies)


@pytest.fixture(scope="module")
def on_php_inputs():
    return replayed(engine(False))


@pytest.fixture(scope="module")
def on_django_inputs():
    return replayed(engine(True))


def expected_django_reply(fixture, name):
    """A chart deviation's expected body, or else PHP's reply in
    `django_reply` form. A chart deviation records only what the service
    computes (#31 ports the rest of the reply), or for Q25 the refusal's
    code. Q5 leaves the chart PHP's."""
    quirk, deviation = transcript_deviations(fixture).get(name, (None, None))
    if quirk not in CHART_QUIRKS:
        return PHP[fixture, name][0]
    return json.loads(deviation["body"])


def test_every_recorded_reschedule_is_replayed(on_php_inputs, on_django_inputs):
    assert list(on_php_inputs) == list(PHP)
    assert list(on_django_inputs) == list(PHP)


@pytest.mark.parametrize("key", list(PHP), ids=IDS)
def test_php_inputs_give_the_php_reply(on_php_inputs, key):
    """The chart and the `timing.internal` keys, in PHP's order."""
    quirk, _ = transcript_deviations(key[0]).get(key[1], (None, None))
    expected = expected_django_reply(*key) if quirk == "Q21" else PHP[key][0]
    assert on_php_inputs[key].django() == expected


@pytest.mark.parametrize("key", list(PHP), ids=IDS)
def test_django_inputs_give_the_php_chart_or_the_deviation(on_django_inputs, key):
    assert on_django_inputs[key].django() == expected_django_reply(*key)


def test_django_keeps_the_running_and_staged_heats(on_django_inputs):
    kept = 0
    for key, reply in on_django_inputs.items():
        for number, before in reply.kept.items():
            assert reply.chart[number - 1] == before, (key, number)
            kept += 1
    assert kept > 40


def chart_of(reply):
    return None if reply is None else reply.get("chart")


def test_each_q20_deviation_is_a_kept_heat_php_changed(on_django_inputs):
    """D34 explains a Q20 entry only where PHP changed the running heat or the
    staged heat, so any other difference from PHP needs its own quirk."""
    for fixture in FIXTURES:
        for name, (quirk, _) in transcript_deviations(fixture).items():
            if quirk == "Q20":
                reply = on_django_inputs[fixture, name]
                php = chart_of(PHP[fixture, name][0])
                assert php_changed_a_kept_heat(php, reply.kept), name


def test_each_chart_deviation_still_differs_from_php():
    """Q20 and Q21 change the chart, and Q25 refuses the request. Q5 changes
    only the racing state, which the service tests check."""
    for fixture in FIXTURES:
        for name, (quirk, _) in transcript_deviations(fixture).items():
            if quirk in CHART_QUIRKS:
                expected = chart_of(expected_django_reply(fixture, name))
                assert expected != chart_of(PHP[fixture, name][0])
