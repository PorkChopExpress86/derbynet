"""The contact windows of the timer and the replay client, judged on each
leg's own clock (`tools/contact_windows.py`, #218).

Each run here is a hand-written transcript, as in test_transcript_diff.py:
a `timecheck` sets the leg's clock, and the replies show the timer's and the
replay's state.
"""

import json
from datetime import UTC, datetime

from tools import transcript_diff as td

NOW = datetime(2026, 11, 1, 1, 40)
T0 = int(NOW.replace(tzinfo=UTC).timestamp())


def login(at: int) -> str:
    stamp = datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")
    body = {"outcome": {"summary": "success"}, "timecheck": stamp}
    return f"login RaceCoordinator\n\n{json.dumps(body)}"


def replay_state(contact: int, age: int | None) -> dict:
    """Connected `age` s after `contact`, or NOT CONNECTED for None."""
    if age is None:
        return {
            "last_contact": contact,
            "state": 1,
            "icon": "img/status/not_connected.png",
            "connected": False,
            "message": "NOT CONNECTED",
        }
    return {
        "last_contact": contact,
        "state": 2,
        "icon": "img/status/ok.png",
        "connected": True,
        "message": f"Ready ({age} sec.)",
    }


def timer_state(contact: int, age: int | None) -> dict:
    connected = age is not None
    return {
        "lanes": 4,
        "last-contact": contact,
        "state": 2 if connected else 1,
        "icon": "img/status/ok.png" if connected else "img/status/not_connected.png",
        "remote-start": False,
        "message": f"CONNECTED ({age} sec.)" if connected else "NOT CONNECTED",
    }


def poll(**states) -> str:
    body = {key.replace("_", "-"): value for key, value in states.items()}
    return f"action.php?query=poll.coordinator\n\n{json.dumps(body)}"


def replay_message(commands: list[str]) -> str:
    body = {"outcome": {"summary": "success"}, "replay": commands}
    return f"post action.php action=replay.message&status=0\n\n{json.dumps(body)}"


def transcript(*exchanges: str) -> str:
    return "".join(f"\n{exchange}\n" for exchange in exchanges)


def compare(php: list[str], django: list[str]) -> td.Report:
    return td.diff(transcript(*php), transcript(*django), now=NOW)


def window_reasons(report: td.Report) -> list[str]:
    return [d.allowed_by for d in report.allowed if "contact window" in d.allowed_by]


# ----- a connected state against NOT CONNECTED ------------------------------


def test_a_replay_state_each_clock_gives_is_allowed_with_both_ages():
    # PHP's replay polled 4 s ago; Django's 30 s ago, past the 10 s window.
    php = [login(T0), poll(replay_state=replay_state(T0 - 4, 4))]
    django = [login(T0), poll(replay_state=replay_state(T0 - 30, None))]

    report = compare(php, django)

    assert report.ok
    reasons = window_reasons(report)
    assert len(reasons) == 4  # state, icon, connected, message
    assert "PHP connected, last contact 4 s before" in reasons[0]
    assert "Django NOT CONNECTED, last contact at least 29 s before" in reasons[0]


def test_not_connected_while_the_legs_own_clock_says_the_window_is_open_fails():
    # Django's later timecheck shows the reply came at most 3 s after the
    # contact, so its window was still open: NOT CONNECTED is wrong.
    php = [login(T0), poll(replay_state=replay_state(T0 - 4, 4)), login(T0 + 1)]
    django = [
        login(T0),
        poll(replay_state=replay_state(T0 - 2, None)),
        login(T0 + 1),
    ]

    report = compare(php, django)

    assert not report.ok
    assert {d.path for d in report.differences} >= {"$.replay-state.state"}
    assert window_reasons(report) == []


def test_connected_past_the_window_by_its_own_age_fails():
    php = [login(T0), poll(replay_state=replay_state(T0 - 15, 15))]
    django = [login(T0), poll(replay_state=replay_state(T0 - 30, None))]

    assert not compare(php, django).ok


def test_a_timer_each_clock_gives_is_allowed():
    # PHP's timer was last heard 67 s ago, Django's 26 s ago: 60 s window.
    php = [login(T0), poll(timer_state=timer_state(T0 - 67, None))]
    django = [login(T0), poll(timer_state=timer_state(T0 - 26, 26))]

    report = compare(php, django)

    assert report.ok
    assert len(window_reasons(report)) == 3  # state, icon, message


def test_a_timer_connected_past_its_window_fails():
    php = [login(T0), poll(timer_state=timer_state(T0 - 67, None))]
    django = [login(T0), poll(timer_state=timer_state(T0 - 61, 61))]

    assert not compare(php, django).ok


def test_the_timers_other_keys_are_still_compared():
    php = [login(T0), poll(timer_state=timer_state(T0 - 67, None))]
    lanes = {**timer_state(T0 - 26, 26), "lanes": 3}
    django = [login(T0), poll(timer_state=lanes)]

    report = compare(php, django)

    assert [d.path for d in report.differences] == ["$.timer-state.lanes"]


# ----- HELLO ----------------------------------------------------------------


def _hello_runs(django_contact: int):
    php = [
        login(T0),
        poll(replay_state=replay_state(T0 - 4, 4)),
        replay_message([]),
        login(T0 + 1),
    ]
    django_state = replay_state(django_contact, None)
    if T0 - django_contact <= 10:
        django_state = replay_state(django_contact, T0 - django_contact)
    django = [
        login(T0),
        poll(replay_state=django_state),
        replay_message(["HELLO"]),
        login(T0 + 1),
    ]
    return php, django


def test_hello_is_allowed_when_that_legs_window_closed_by_its_own_clock():
    php, django = _hello_runs(T0 - 30)

    report = compare(php, django)

    assert report.ok
    [hello] = [d for d in report.allowed if d.path == "$.replay[0]"]
    assert "Django says HELLO" in hello.allowed_by


def test_hello_fails_when_that_legs_window_is_open_by_its_own_clock():
    # Django's replay polled 3 s before, and its clock shows the HELLO poll
    # came at most 4 s after: its window was open, so HELLO is wrong.
    php, django = _hello_runs(T0 - 3)

    report = compare(php, django)

    assert [d.path for d in report.differences] == ["$.replay[0]"]


def test_no_hello_fails_when_that_legs_window_closed_by_its_own_clock():
    # PHP's replay was last heard 30 s ago and it sends no HELLO.
    php = [
        login(T0),
        poll(replay_state=replay_state(T0 - 30, None)),
        replay_message([]),
    ]
    django = [
        login(T0),
        poll(replay_state=replay_state(T0 - 40, None)),
        replay_message(["HELLO"]),
    ]

    report = compare(php, django)

    assert [d.path for d in report.differences] == ["$.replay[0]"]


# ----- commands queued while one window was shut ---------------------------


def _queue_runs(django_lapsed: bool):
    start = [
        login(T0),
        poll(replay_state=replay_state(T0 - 1, 1)),
        replay_message([]),
    ]
    php = [*start, poll(replay_state=replay_state(T0, 1)), replay_message(["A", "B"])]
    django_state = replay_state(T0 - 1, None if django_lapsed else 2)
    django = [*start, poll(replay_state=django_state), replay_message(["A"])]
    return php, django


def test_a_queue_cut_short_after_that_leg_showed_its_window_shut_is_allowed():
    php, django = _queue_runs(django_lapsed=True)

    report = compare(php, django)

    assert report.ok
    [dropped] = [d for d in report.allowed if d.path == "$.replay[1]"]
    assert "Django showed its replay NOT CONNECTED" in dropped.allowed_by


def test_a_queue_cut_short_with_the_window_open_fails():
    php, django = _queue_runs(django_lapsed=False)

    report = compare(php, django)

    assert "$.replay[1]" in [d.path for d in report.differences]
