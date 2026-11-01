"""The parity gate's verdict (#37) on hand-written legs.

A leg is what `testing/steps.sh` writes with `PARITY_DIR` set: `steps.tsv`
and one transcript per step, in the form `testing/common.sh` writes.
"""

from datetime import datetime

import pytest

from legacy_api.missing import MISSING
from legacy_api.registry import (
    PARITY_PATHS,
    REGISTRY,
    ParityPath,
    allowed_by,
    divergence_text,
)
from tools import parity_gate as gate

NOW = datetime(2026, 11, 1, 12, 0)

POLL = "action.php?query=poll.coordinator"


def poll(heat="1", classid="3"):
    return (
        f"\n{POLL}\n\n"
        f'{{"current-heat": {{"now_racing": false, "heat": {heat},'
        f' "classid": {classid}}}}}\n'
    )


def leg(tmp_path, name, steps):
    """`steps` is a list of (label, outcome, attempts, transcript, reason)."""
    directory = tmp_path / name
    directory.mkdir()
    lines = []
    for n, (label, outcome, attempts, transcript, reason) in enumerate(steps, 1):
        file = ""
        if transcript is not None:
            file = f"{n:02d}-step.curl"
            (directory / file).write_text(transcript)
        lines.append(f"{n}\t{label}\t{outcome}\t{attempts}\t{file}\t{reason}\n")
    (directory / "steps.tsv").write_text("".join(lines))
    return gate.read_leg(directory)


def judge(php, django, **kwargs):
    kwargs.setdefault("waiting", {})
    kwargs.setdefault("step_divergences", {})
    kwargs.setdefault("after_skip", {})
    kwargs.setdefault("skips", {})
    kwargs.setdefault("allow", lambda difference: None)
    return gate.judge(php, django, NOW, **kwargs)


def test_matching_legs_pass(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "pass", 1, poll(), "")])

    [result] = judge(php, django)

    assert not result.failed
    assert result.matches


def test_a_changed_key_fails_and_names_its_path(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(
        tmp_path,
        "django",
        [("test-a.sh", "pass", 1, poll().replace('"heat"', '"heat-number"'), "")],
    )

    [result] = judge(php, django)

    assert result.problems == ["2 differences from PHP"]
    rendered = gate.render([result], ("php", "django"))
    assert "$.current-heat.heat: php=1 django=(missing)" in rendered
    assert "$.current-heat.heat-number: php=(missing) django=1" in rendered


def test_a_failed_step_fails_on_either_leg(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "fail", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "fail", 1, poll(), "")])

    [result] = judge(php, django)

    assert result.problems == ["fails on PHP", "fails on Django"]


def test_a_waiting_step_may_fail_and_stop_early(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll() + poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "fail", 1, poll(), "")])

    [result] = judge(php, django, waiting={"test-a.sh": gate.Waiting("#49")})
    [unlisted] = judge(php, django)

    assert not result.failed
    assert "waiting on #49" in gate.render([result], ("php", "django"))
    assert unlisted.problems == ["fails on Django", "stops before PHP's run ends"]


def test_a_waiting_step_still_fails_on_a_reply_that_differs(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "fail", 1, poll(heat="2"), "")])

    [result] = judge(php, django, waiting={"test-a.sh": gate.Waiting("#49")})

    assert result.problems == ["1 differences from PHP"]


def test_a_waiting_step_whose_requests_part_fails(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    other = "\naction.php?query=poll.kiosk\n\n{}\n"
    django = leg(tmp_path, "django", [("test-a.sh", "fail", 1, other, "")])

    [result] = judge(php, django, waiting={"test-a.sh": gate.Waiting("#49")})

    assert result.problems == ["the requests part from PHP's"]


def test_a_waiting_step_that_passes_fails_the_gate(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "pass", 1, poll(), "")])

    [result] = judge(php, django, waiting={"test-a.sh": gate.Waiting("#49")})

    assert result.failed
    assert "remove it from WAITING" in result.problems[0]


def test_a_waiting_entry_no_leg_ran_fails_the_gate(tmp_path):
    php = leg(tmp_path, "php", [])
    django = leg(tmp_path, "django", [])

    [result] = judge(php, django, waiting={"test-gone.sh": gate.Waiting("#49")})

    assert result.failed


def test_a_skip_is_reported_and_not_compared(tmp_path):
    php = leg(tmp_path, "php", [("test-balloting.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-balloting.sh", "skip", 0, None, "#54")])

    [result] = judge(php, django, skips={"test-balloting.sh": "#54"})

    assert not result.failed
    assert result.report is None
    assert "skip (#54)" in gate.render([result], ("php", "django"))


def test_a_step_after_a_skip_may_differ_but_not_match(tmp_path):
    php = leg(tmp_path, "php", [("snapshot_get", "pass", 1, poll(), "")])
    differs = leg(tmp_path, "differs", [("snapshot_get", "pass", 1, poll("2"), "")])
    matches = leg(tmp_path, "matches", [("snapshot_get", "pass", 1, poll(), "")])
    skips = {"snapshot_get": "#54"}

    [differed] = judge(php, differs, after_skip=skips)
    [matched] = judge(php, matches, after_skip=skips)

    assert not differed.failed
    assert "follows a skip: #54" in gate.render([differed], ("php", "django"))
    assert "remove it from AFTER_SKIP" in matched.problems[0]


def test_the_summary_groups_failing_differences_by_path(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll() + poll(), "")])
    django = leg(
        tmp_path, "django", [("test-a.sh", "pass", 1, poll("2") + poll("3"), "")]
    )

    rendered = gate.render(judge(php, django), ("php", "django"))

    assert "| `query poll.coordinator` | `$.current-heat.heat` | 2 |" in rendered


def test_a_step_one_leg_never_ran_fails(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-b.sh", "pass", 1, poll(), "")])

    results = judge(php, django)

    assert [r.problems for r in results] == [
        ["the Django leg never ran it"],
        ["the PHP leg never ran it"],
    ]


def test_a_retry_is_reported(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "pass", 2, poll(), "")])

    results = judge(php, django)

    assert not results[0].failed
    rendered = gate.render(results, ("php", "django"))
    assert "pass after a retry" in rendered
    assert "Retried once: django `test-a.sh`." in rendered


def test_requests_that_part_on_purpose_are_compared_up_to_there(tmp_path):
    php = leg(tmp_path, "php", [("test-r.sh", "pass", 1, poll() + poll(), "")])
    django = leg(
        tmp_path,
        "django",
        [("test-r.sh", "pass", 1, poll() + "\nexport-json.php\n\n{}\n", "")],
    )

    [parted] = judge(php, django, step_divergences={"test-r.sh": "D34"})
    [unexplained] = judge(php, django)

    assert not parted.failed
    assert unexplained.problems == ["the requests part from PHP's"]


def test_an_allowed_difference_is_listed_and_passes(tmp_path):
    php = leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(classid="-1"), "")])
    django = leg(tmp_path, "django", [("test-a.sh", "pass", 1, poll("null"), "")])

    def allow(difference):
        return "R3: null" if difference.path.endswith(".heat") else None

    [result] = judge(php, django, allow=allow)

    assert result.problems == ["1 differences from PHP"]
    assert [d.path for d in result.report.allowed] == ["$.current-heat.heat"]


def test_main_reports_a_leg_with_no_steps(tmp_path, capsys):
    (tmp_path / "php").mkdir()
    (tmp_path / "django").mkdir()

    assert gate.main([str(tmp_path / "php"), str(tmp_path / "django")]) == 1


def test_main_writes_the_summary_and_exits_on_the_verdict(tmp_path):
    leg(tmp_path, "php", [("test-balloting.sh", "pass", 1, poll(), "")])
    leg(tmp_path, "django", [("test-balloting.sh", "pass", 1, poll(heat="2"), "")])
    summary = tmp_path / "summary.md"

    status = gate.main(
        [str(tmp_path / "php"), str(tmp_path / "django"), "--summary", str(summary)]
    )

    assert status == 1
    assert "$.current-heat.heat: php=1 django=2" in summary.read_text()


# Found in the review of PR #159


RACERS = (
    "\naction.php?query=racer.list\n\n"
    '{"racers": [{"racerid": 11, "firstname": "Adam"},'
    ' {"racerid": 12, "firstname": "Bea"}]}\n'
)


def names_racer(racerid):
    return f'\n{POLL}\n\n{{"current-heat": {{"racerid": {racerid}}}}}\n'


def test_ids_keep_their_numbers_from_one_step_to_the_next(tmp_path):
    php = leg(
        tmp_path,
        "php",
        [("a.sh", "pass", 1, RACERS, ""), ("b.sh", "pass", 1, names_racer(11), "")],
    )
    django = leg(
        tmp_path,
        "django",
        [("a.sh", "pass", 1, RACERS, ""), ("b.sh", "pass", 1, names_racer(12), "")],
    )

    first, second = judge(php, django)

    assert not first.failed
    assert second.problems == ["1 differences from PHP"]
    assert second.report.differences[0].path == "$.current-heat.racerid"


def test_a_reset_numbers_ids_afresh_for_the_next_steps(tmp_path):
    reset = (
        "\npost action.php action=database.execute&script=schema\n\n"
        '{"outcome": {"summary": "success"}}\n'
    )
    # After the reset PHP reuses id 11 for a new racer; Django hands out 13.
    php = leg(
        tmp_path,
        "php",
        [
            ("a.sh", "pass", 1, RACERS, ""),
            ("reset.sh", "pass", 1, reset, ""),
            ("b.sh", "pass", 1, names_racer(11), ""),
        ],
    )
    django = leg(
        tmp_path,
        "django",
        [
            ("a.sh", "pass", 1, RACERS, ""),
            ("reset.sh", "pass", 1, reset, ""),
            ("b.sh", "pass", 1, names_racer(13), ""),
        ],
    )

    assert not any(r.failed for r in judge(php, django))


def test_ids_a_skipped_step_shows_carry_on_in_the_leg_that_ran_it(tmp_path):
    php = leg(
        tmp_path,
        "php",
        [
            ("test-balloting.sh", "pass", 1, RACERS, ""),
            ("b.sh", "pass", 1, names_racer(12), ""),
        ],
    )
    django = leg(
        tmp_path,
        "django",
        [
            ("test-balloting.sh", "skip", 0, None, "#54"),
            ("b.sh", "pass", 1, names_racer(12), ""),
        ],
    )

    _, after = judge(php, django, skips={"test-balloting.sh": "#54"})

    # PHP's 12 is its second racer; Django's is its first.
    assert after.problems == ["1 differences from PHP"]


def test_only_a_listed_django_skip_passes(tmp_path):
    php = leg(tmp_path, "php", [("test-basic-racing.sh", "pass", 1, RACERS, "")])
    skipped = leg(
        tmp_path, "django", [("test-basic-racing.sh", "skip", 0, None, "whatever")]
    )

    [unlisted] = judge(php, skipped)
    [listed] = judge(php, skipped, skips={"test-basic-racing.sh": "#49"})

    assert unlisted.problems == ["skipped on Django, and SKIPS doesn't list it"]
    assert not listed.failed


def test_a_php_skip_fails_and_django_is_still_judged(tmp_path):
    php = leg(tmp_path, "php", [("test-basic-racing.sh", "skip", 0, None, "x")])
    django = leg(tmp_path, "django", [("test-basic-racing.sh", "fail", 1, RACERS, "")])

    [result] = judge(php, django, skips={"test-basic-racing.sh": "#49"})

    assert "skipped on PHP, which skips nothing" in result.problems


def test_a_listed_skip_that_django_ran_fails(tmp_path):
    php = leg(tmp_path, "php", [("test-balloting.sh", "pass", 1, RACERS, "")])
    django = leg(tmp_path, "django", [("test-balloting.sh", "pass", 1, RACERS, "")])

    [result] = judge(php, django, skips={"test-balloting.sh": "#54"})

    assert result.problems == ["SKIPS lists it but Django ran it: remove it from SKIPS"]


def test_a_step_divergence_that_no_longer_parts_fails(tmp_path):
    php = leg(tmp_path, "php", [("test-reschedule.sh", "pass", 1, RACERS, "")])
    django = leg(tmp_path, "django", [("test-reschedule.sh", "pass", 1, RACERS, "")])

    [result] = judge(php, django, step_divergences={"test-reschedule.sh": "D34"})

    assert "remove it from STEP_DIVERGENCES" in result.problems[0]


HISTORY_RUN = (
    "\nracer.php\n\n<html/>\n"
    '\naction.php?query=racer.list\n\n{"racers": []}\n'
    "\nhistory.php\n\n<html/>\n"
    "\nhistory.php?again\n\n<html/>\n"
)


def test_a_waiting_step_may_stop_only_after_its_request(tmp_path):
    php = leg(tmp_path, "php", [("test-basic-racing.sh", "pass", 1, HISTORY_RUN, "")])
    early = HISTORY_RUN.split("\naction.php")[0]
    at_history = HISTORY_RUN.split("\nhistory.php?again")[0]
    stops_early = leg(
        tmp_path, "early", [("test-basic-racing.sh", "fail", 1, early, "")]
    )
    stops_after = leg(
        tmp_path, "after", [("test-basic-racing.sh", "fail", 1, at_history, "")]
    )
    waits = {"test-basic-racing.sh": gate.Waiting("#49", "history.php")}

    [before] = judge(php, stops_early, waiting=waits)
    [after] = judge(php, stops_after, waiting=waits)

    assert before.problems == ["stops before it sends history.php, which it waits on"]
    assert not after.failed


def complete(tmp_path, name, count):
    (tmp_path / name / "complete").write_text(f"{count}\n")


def test_main_fails_a_leg_that_was_cut_short(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "WAITING", {})
    monkeypatch.setattr(gate, "SKIPS", {})
    leg(tmp_path, "php", [("test-a.sh", "pass", 1, poll(), "")])
    leg(tmp_path, "django", [("test-a.sh", "pass", 1, poll(), "")])
    args = [str(tmp_path / "php"), str(tmp_path / "django")]

    complete(tmp_path, "php", 1)
    no_file = gate.main(args)
    complete(tmp_path, "django", 2)
    wrong_count = gate.main(args)
    complete(tmp_path, "django", 1)
    whole = gate.main(args)

    assert (no_file, wrong_count, whole) == (1, 1, 0)


# The registry side: PARITY_PATHS


def test_r9_allows_only_the_positional_columns_django_lacks():
    found = "query poll.results"

    assert allowed_by(found, '$.results[0]["3"]', 5, MISSING).startswith("R9: ")
    assert allowed_by(found, '$.results[0]["3"]', 5, 9) is None
    assert allowed_by(found, '$.results[0]["3"]', MISSING, 9) is None
    assert allowed_by(found, '$.results[0]["8"]', 5, MISSING) is None
    assert allowed_by(found, '$.results[0]["car number"]', "1", MISSING) is None


def test_c19_and_c20_allow_only_the_type_django_gives():
    kiosks = "query poll.kiosk.all"

    assert allowed_by(kiosks, "$.current-scene", "", 7).startswith("C19: ")
    assert allowed_by(kiosks, "$.current-scene", "", None) is None
    assert allowed_by(kiosks, "$.current-scene", "", "7") is None
    assert allowed_by(kiosks, "$.kiosks[0].parameters", None, "{}").startswith("C20")
    assert allowed_by(kiosks, "$.kiosks[0].parameters", None, 3) is None


def test_a_parity_path_matches_any_index_and_any_run():
    allowed = ParityPath("query poll", "$.racers[*].car*", "R3")

    assert allowed.covers("query poll", "$.racers[12].carnumber")
    assert not allowed.covers("query poll", "$.racers[x].carnumber")
    assert not allowed.covers("query poll.coordinator", "$.racers[1].carnumber")


def test_allowed_by_names_the_endpoints_divergence(monkeypatch):
    monkeypatch.setattr(
        "legacy_api.registry.PARITY_PATHS",
        (ParityPath("query poll.coordinator", "$.current-heat.classid", "R3"),),
    )

    assert allowed_by("query poll.coordinator", "$.current-heat.classid").startswith(
        "R3: "
    )
    assert allowed_by("query poll.coordinator", "$.current-heat.heat") is None


def test_a_parity_path_with_values_allows_only_those():
    allowed = ParityPath("query poll", "$.current-heat.heat", "R3", php=-1, django=None)

    assert allowed.covers("query poll", "$.current-heat.heat", -1, None)
    assert not allowed.covers("query poll", "$.current-heat.heat", -1, 3)
    # JSON's false is not 0.
    assert not allowed.covers("query poll", "$.current-heat.heat", -1, False)


def test_r3_allows_null_for_minus_one_and_nothing_else():
    path = "$.current-heat.heat"

    assert allowed_by("query poll.coordinator", path, -1, None).startswith("R3: ")
    assert allowed_by("query poll.coordinator", path, 1, 2) is None
    assert allowed_by("query poll.coordinator", "$.refused-results", 1, 2) is None


@pytest.mark.parametrize("allowed", PARITY_PATHS, ids=str)
def test_every_parity_path_cites_a_divergence_of_its_endpoint(allowed):
    kind, _, name = allowed.endpoint.partition(" ")

    assert REGISTRY.get(kind, name) is not None, allowed.endpoint
    assert divergence_text(allowed.endpoint, allowed.divergence), allowed


@pytest.mark.parametrize(
    "endpoint",
    [
        "action award.edit",
        "action award.delete",
        "action award.order",
        "action award.adhoc",
        "action award.winner",
        "query award.list",
    ],
)
def test_a1_allows_class_and_rank_0_in_every_reply_that_lists_the_awards(endpoint):
    for key in ("classid", "rankid"):
        path = f"$.awards[3].{key}"
        assert allowed_by(endpoint, path, None, 0).startswith("A1: ")
        assert allowed_by(endpoint, path, None, 2) is None
        assert allowed_by(endpoint, path, 1, 0) is None


@pytest.mark.parametrize(
    "endpoint", ["action heat.reinstate", "query poll.coordinator"]
)
def test_q4_allows_the_place_a_reinstate_brings_back(endpoint):
    path = "$.racers[2].finishplace"

    assert allowed_by(endpoint, path, "", 3).startswith("Q4: ")
    assert allowed_by(endpoint, path, 2, 3) is None
    assert allowed_by(endpoint, path, "", "3") is None
    assert allowed_by("action heat.select", path, "", 3) is None
