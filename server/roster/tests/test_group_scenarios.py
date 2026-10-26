"""The group scripts of the shell suite, replayed through the services.

`http/suite/<script>.http` holds, after each request that changes the
structure, what changed in the classes, ranks, partitions, rounds and
racers, keyed by name (#7). Each step here posts the same request and
compares the whole state.
"""

import json

import pytest

from racing.engine.tests.golden import GOLDEN_DIR, read_transcript
from roster.tests.php_replay import Replay, expected_after, recorded_state

pytestmark = pytest.mark.django_db

# The requests each script expects to fail (`check_jfailure`). The scripts
# check only that they fail; the code is the one PHP's checks give.
EXPECTED_FAILURES = {
    "partitions": [
        ("partition.edit", "name-taken"),
        ("partition.add", "already-exists"),
    ],
    "racing-groups": [],
    "den-changes-basic": [("rank.delete", "last-rank")],
    "single-group-operation": [],
}


def _starts_with_reset(exchanges):
    """True when the script first empties the database, so the state the
    previous script left needs no rebuilding."""
    first = list(exchanges.values())[1]
    return first["request-body"] == "action=database.execute&script=schema"


@pytest.mark.parametrize("fixture", EXPECTED_FAILURES)
def test_script_leaves_the_state_php_recorded_after_every_step(fixture):
    replay = Replay()
    exchanges = read_transcript(GOLDEN_DIR / "http" / "suite" / f"{fixture}.http")
    expected = None
    for name, exchange in exchanges.items():
        body = json.loads(exchange["body"])
        if expected is None:
            expected = recorded_state(body["state"])
            if not _starts_with_reset(exchanges):
                replay.seed(body["state"])
                assert replay.state() == expected, f"{fixture} {name}"
            continue
        replay.post(exchange["request-body"])
        expected = expected_after(expected, body["changed"])
        assert replay.state() == expected, f"{fixture} {name}"
    assert replay.failures == EXPECTED_FAILURES[fixture]
