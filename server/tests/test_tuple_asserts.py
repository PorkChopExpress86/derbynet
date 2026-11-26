"""An assert compares one value, not a tuple built of several (#501 and
#514 retros).

`assert (now.current_round_id, now.current_heat, now.racing) == (7, 2, True)`
fails naming an index of the tuple (`At index 2 diff`), not the field. #501
wrote six in a new test file, and #514 copied one from `timed()` after its
brief asked for no compound assert; both reviews sent them back. Write one
assert per field.

An `==` or `!=` counts when either side is a tuple or list literal of two
or more values with one that is not a constant: `x == (a, b)`,
`[a, b] == [1, 2]` and `(1, 2) == (a, b)` too (PR #521's review). A
literal of constants alone, `x == (1, 2)`, is the one value `x` should be,
and `()`, `(a,)` and `(a, b) in pairs` are left alone.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent

# Each test file whose tuple asserts predate this check, with how many it
# holds; not reviewed against the rule. Lower a count or remove an entry
# when its file changes; never add one or raise one. KNOWN_FILES and
# KNOWN_ASSERTS are this list's size the day it was written, so an entry
# added fails.
KNOWN = {
    "accounts/tests/test_import_roles.py": 1,
    "awards/tests/test_judging.py": 8,
    "core/tests/test_event_lifecycle.py": 1,
    "core/tests/test_events.py": 1,
    "core/tests/test_group_settings.py": 2,
    "core/tests/test_legacy_keys.py": 1,
    "core/tests/test_reset_event.py": 2,
    "kiosks/tests/test_page_services.py": 6,
    "kiosks/tests/test_pages.py": 1,
    "legacy_api/tests/test_awards_parity.py": 1,
    "legacy_api/tests/test_ballot_api.py": 4,
    "legacy_api/tests/test_car_ranges.py": 1,
    "legacy_api/tests/test_database_check.py": 1,
    "legacy_api/tests/test_database_execute.py": 1,
    "legacy_api/tests/test_database_purge.py": 1,
    "legacy_api/tests/test_export_json.py": 3,
    "legacy_api/tests/test_groups_api.py": 10,
    "legacy_api/tests/test_kiosks.py": 1,
    "legacy_api/tests/test_playlist_api.py": 4,
    "legacy_api/tests/test_playlist_race_day.py": 1,
    "legacy_api/tests/test_preferences.py": 3,
    "legacy_api/tests/test_race_control.py": 1,
    "legacy_api/tests/test_race_control_concurrency.py": 2,
    "legacy_api/tests/test_racers.py": 6,
    "legacy_api/tests/test_read_only_event.py": 2,
    "legacy_api/tests/test_replay_concurrency.py": 2,
    "legacy_api/tests/test_replay_messages.py": 1,
    "legacy_api/tests/test_result_import.py": 2,
    "legacy_api/tests/test_snapshot.py": 3,
    "legacy_api/tests/test_standings.py": 2,
    "legacy_api/tests/test_timer.py": 10,
    "legacy_api/tests/test_timer_log.py": 1,
    "media/tests/test_photo_api.py": 2,
    "media/tests/test_photo_inventory.py": 6,
    "media/tests/test_photo_limits.py": 1,
    "media/tests/test_photo_routes.py": 5,
    "racing/engine/tests/test_close_lanes.py": 1,
    "racing/engine/tests/test_current_award_keys.py": 1,
    "racing/engine/tests/test_finalists_top_zero.py": 1,
    "racing/engine/tests/test_heat_state.py": 21,
    "racing/engine/tests/test_lane_bias.py": 3,
    "racing/engine/tests/test_results.py": 15,
    "racing/engine/tests/test_speed_awards_parity.py": 1,
    "racing/services/tests/test_audit.py": 3,
    "racing/services/tests/test_finals.py": 22,
    "racing/services/tests/test_heat_concurrency.py": 3,
    "racing/services/tests/test_heat_state_parity.py": 1,
    "racing/services/tests/test_heats.py": 19,
    "racing/services/tests/test_polls.py": 5,
    "racing/services/tests/test_reschedule.py": 17,
    "racing/services/tests/test_restore_results.py": 8,
    "racing/services/tests/test_rounds.py": 3,
    "racing/services/tests/test_schedule.py": 4,
    "racing/services/tests/test_standings.py": 2,
    "racing/services/tests/test_timer_transcripts.py": 5,
    "racing/tests/test_all_zero_lanes.py": 2,
    "racing/tests/test_did_not_finish.py": 2,
    "racing/tests/test_playlist.py": 4,
    "racing/tests/test_playlist_page.py": 4,
    "racing/tests/test_playlist_script.py": 6,
    "racing/tests/test_race_day.py": 4,
    "racing/tests/test_result_changes_rebuild_the_final.py": 5,
    "racing/tests/test_timer_test_lanes.py": 1,
    "roster/tests/test_award_settings.py": 4,
    "roster/tests/test_barcode.py": 1,
    "roster/tests/test_car_numbers.py": 1,
    "roster/tests/test_groups.py": 1,
    "roster/tests/test_weigh_in.py": 7,
    "tests/test_detach.py": 8,
    "tests/test_docstring_claims.py": 12,
    "tests/test_guide_commands.py": 5,
    "tests/test_load_test.py": 9,
    "tests/test_network_changed.py": 4,
    "tests/test_opens_oracle.py": 1,
    "tests/test_operand_mutants.py": 1,
    "tests/test_parity_gate.py": 2,
    "tests/test_pytest_under_load.py": 4,
    "tests/test_settings.py": 2,
    "tests/test_source_checks.py": 1,
    "tests/test_transcript_diff.py": 19,
    "tests/test_transcript_diff_live.py": 1,
    "transfer/tests/test_batch_import.py": 1,
    "transfer/tests/test_export.py": 1,
    "transfer/tests/test_import_rules.py": 4,
    "transfer/tests/test_legacy_units.py": 2,
    "ui/tests/browser/test_awards.py": 4,
    "ui/tests/browser/test_checkin.py": 3,
    "ui/tests/browser/test_dashboard.py": 2,
    "ui/tests/browser/test_groups.py": 3,
    "ui/tests/browser/test_harness.py": 1,
    "ui/tests/browser/test_photos.py": 1,
    "ui/tests/browser/test_playlist.py": 2,
    "ui/tests/browser/test_replay.py": 1,
    "ui/tests/browser/test_timer.py": 1,
    "ui/tests/test_awards_pages.py": 2,
    "ui/tests/test_browser_sizes.py": 2,
    "ui/tests/test_checkin.py": 5,
    "ui/tests/test_dashboard.py": 14,
    "ui/tests/test_events_page.py": 1,
    "ui/tests/test_export_files.py": 1,
    "ui/tests/test_groups.py": 5,
    "ui/tests/test_import_pages.py": 1,
    "ui/tests/test_navigation.py": 1,
    "ui/tests/test_photos.py": 2,
    "ui/tests/test_playlist.py": 1,
    "ui/tests/test_poll_signatures.py": 1,
    "ui/tests/test_race_day_screens.py": 1,
    "ui/tests/test_replay_pages.py": 1,
    "ui/tests/test_timer_pages.py": 2,
}
KNOWN_FILES = 110
KNOWN_ASSERTS = 415


def constant(node: ast.expr) -> bool:
    """A literal of constants only: `7`, `-1`, `("Scouts", 1)`."""
    if isinstance(node, ast.UnaryOp):
        node = node.operand
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(constant(element) for element in node.elts)
    return isinstance(node, ast.Constant)


def built(node: ast.expr) -> bool:
    """A tuple or list literal of two or more, not all constants."""
    return (
        isinstance(node, (ast.Tuple, ast.List))
        and len(node.elts) >= 2
        and not constant(node)
    )


def tuple_asserts(source: str) -> list[int]:
    """The line of each assert whose `==` or `!=` has, on either side, a
    tuple or list built of several values."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.test.ops)
        and any(built(side) for side in (node.test.left, *node.test.comparators))
    ]


def counts() -> dict[str, list[int]]:
    found = {}
    for path in sorted(SERVER.rglob("test_*.py")):
        if ".venv" in path.relative_to(SERVER).parts:
            continue
        if lines := tuple_asserts(path.read_text(encoding="utf-8")):
            found[path.relative_to(SERVER).as_posix()] = lines
    return found


def test_a_tuple_assert_is_found():
    source = """
def test_one(now, x, a, b):
    assert (now.round, now.heat, now.racing) == (7, 2, True)
    assert (
        now.round,
        now.heat,
    ) != (7, 2)
    assert x == (a, b)
    assert [a, b] == [1, 2]
    assert (1, 2) == (a, b)
    assert x == [(a, 1), (b, 2)]
    assert now.round == 7
    assert now.pair == (7, 2)
    assert now.pair == ("Scouts", -1, (2, 3))
    assert x == ()
    assert x == (a,)
    assert (now.round, now.heat) in {(7, 2), (7, 3)}
    assert (now.round, now.heat) not in []
    assert (1, a) < (1, b)

def timed(heats):
    assert (heats[0], heats[1]) == (1, 2)
"""
    assert tuple_asserts(source) == [3, 4, 8, 9, 10, 11, 22]


def added(found: dict[str, list[int]], known: dict[str, int]) -> list[str]:
    """Each file of `found` that holds more tuple asserts than `known` lets."""
    return [
        f"{name}: lines {lines}"
        for name, lines in found.items()
        if len(lines) > known.get(name, 0)
    ]


def stale(found: dict[str, list[int]], known: dict[str, int]) -> dict:
    """Each `known` file that now holds fewer: (known, held)."""
    return {
        name: (count, len(found.get(name, [])))
        for name, count in known.items()
        if len(found.get(name, [])) < count
    }


def test_a_new_file_or_a_raised_count_is_added_and_a_lowered_one_stale():
    found = {"a.py": [3], "b.py": [4, 9], "c.py": [5]}
    known = {"b.py": 1, "c.py": 1, "d.py": 2}
    assert added(found, known) == ["a.py: lines [3]", "b.py: lines [4, 9]"]
    assert stale(found, known) == {"d.py": (2, 0)}
    assert stale({"d.py": [1], "d2.py": [1, 2]}, {"d.py": 2, "d2.py": 2}) == {
        "d.py": (2, 1)
    }


def test_the_test_files_are_read():
    found = counts()
    assert "racing/tests/test_result_changes_rebuild_the_final.py" in found
    assert not [name for name in found if name.startswith(".venv/")]


def test_no_test_file_adds_a_tuple_assert():
    found = added(counts(), KNOWN)
    assert found == [], (
        "write one assert per field, so a failure names the field "
        f"(tests/test_tuple_asserts.py): {found}"
    )


def test_the_known_tuple_asserts_only_shrink():
    """A KNOWN count that is higher than its file holds is lowered, so the
    list only shrinks."""
    assert stale(counts(), KNOWN) == {}, (
        "lower each count to what its file holds (known, held)"
    )
    assert len(KNOWN) <= KNOWN_FILES
    assert sum(KNOWN.values()) <= KNOWN_ASSERTS
