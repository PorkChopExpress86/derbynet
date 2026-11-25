"""An assert compares one value, not a tuple built of several (#501 and
#514 retros).

`assert (now.current_round_id, now.current_heat, now.racing) == (7, 2, True)`
fails naming an index of the tuple (`At index 2 diff`), not the field. #501
wrote six in a new test file, and #514 copied one from `timed()` after its
brief asked for no compound assert; both reviews sent them back. Write one
assert per field. A tuple on the left of `in`, `(a, b) in pairs`, is one
fact and is left alone.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", "var", "__pycache__"}

# Each test file whose tuple asserts predate this check, with how many it
# holds; not reviewed against the rule. Lower a count or remove an entry
# when its file changes; never add one or raise one. KNOWN_FILES and
# KNOWN_ASSERTS are this list's size the day it was written, so an entry
# added fails.
KNOWN = {
    "awards/tests/test_judging.py": 4,
    "core/tests/test_event_lifecycle.py": 1,
    "core/tests/test_events.py": 1,
    "core/tests/test_group_settings.py": 2,
    "core/tests/test_legacy_keys.py": 1,
    "core/tests/test_reset_event.py": 2,
    "kiosks/tests/test_page_services.py": 6,
    "kiosks/tests/test_pages.py": 1,
    "legacy_api/tests/test_awards_parity.py": 1,
    "legacy_api/tests/test_car_ranges.py": 1,
    "legacy_api/tests/test_database_check.py": 1,
    "legacy_api/tests/test_database_execute.py": 1,
    "legacy_api/tests/test_database_purge.py": 1,
    "legacy_api/tests/test_export_json.py": 2,
    "legacy_api/tests/test_groups_api.py": 3,
    "legacy_api/tests/test_kiosks.py": 1,
    "legacy_api/tests/test_playlist_api.py": 2,
    "legacy_api/tests/test_preferences.py": 3,
    "legacy_api/tests/test_race_control_concurrency.py": 2,
    "legacy_api/tests/test_racers.py": 6,
    "legacy_api/tests/test_read_only_event.py": 2,
    "legacy_api/tests/test_result_import.py": 2,
    "legacy_api/tests/test_snapshot.py": 3,
    "legacy_api/tests/test_timer.py": 5,
    "legacy_api/tests/test_timer_log.py": 1,
    "media/tests/test_photo_api.py": 2,
    "media/tests/test_photo_inventory.py": 6,
    "media/tests/test_photo_limits.py": 1,
    "media/tests/test_photo_routes.py": 5,
    "racing/engine/tests/test_current_award_keys.py": 1,
    "racing/engine/tests/test_finalists_top_zero.py": 1,
    "racing/engine/tests/test_heat_state.py": 2,
    "racing/engine/tests/test_lane_bias.py": 3,
    "racing/engine/tests/test_speed_awards_parity.py": 1,
    "racing/services/tests/test_audit.py": 1,
    "racing/services/tests/test_finals.py": 15,
    "racing/services/tests/test_heats.py": 3,
    "racing/services/tests/test_polls.py": 5,
    "racing/services/tests/test_reschedule.py": 2,
    "racing/services/tests/test_restore_results.py": 1,
    "racing/services/tests/test_rounds.py": 3,
    "racing/services/tests/test_schedule.py": 2,
    "racing/services/tests/test_timer_transcripts.py": 1,
    "racing/tests/test_playlist_page.py": 3,
    "racing/tests/test_race_day.py": 4,
    "racing/tests/test_result_changes_rebuild_the_final.py": 5,
    "racing/tests/test_timer_test_lanes.py": 1,
    "roster/tests/test_award_settings.py": 4,
    "roster/tests/test_barcode.py": 1,
    "roster/tests/test_groups.py": 1,
    "roster/tests/test_weigh_in.py": 7,
    "tests/test_detach.py": 8,
    "tests/test_docstring_claims.py": 1,
    "tests/test_guide_commands.py": 5,
    "tests/test_load_test.py": 9,
    "tests/test_parity_gate.py": 2,
    "tests/test_pi_images.py": 3,
    "tests/test_pytest_under_load.py": 2,
    "tests/test_settings.py": 2,
    "tests/test_transcript_diff.py": 17,
    "tests/test_transcript_diff_live.py": 1,
    "transfer/tests/test_batch_import.py": 1,
    "transfer/tests/test_export.py": 1,
    "transfer/tests/test_import_rules.py": 3,
    "ui/tests/browser/test_awards.py": 3,
    "ui/tests/browser/test_checkin.py": 2,
    "ui/tests/browser/test_groups.py": 3,
    "ui/tests/browser/test_playlist.py": 2,
    "ui/tests/test_awards_pages.py": 1,
    "ui/tests/test_checkin.py": 3,
    "ui/tests/test_dashboard.py": 2,
    "ui/tests/test_events_page.py": 1,
    "ui/tests/test_groups.py": 4,
    "ui/tests/test_import_pages.py": 1,
    "ui/tests/test_playlist.py": 1,
    "ui/tests/test_timer_pages.py": 2,
}
KNOWN_FILES = 76
KNOWN_ASSERTS = 216


def tuple_asserts(source: str) -> list[int]:
    """The line of each assert whose `==` or `!=` has a tuple on its left."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Tuple)
        and all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.test.ops)
    ]


def counts() -> dict[str, list[int]]:
    found = {}
    for path in sorted(SERVER.rglob("test_*.py")):
        if SKIP.intersection(path.relative_to(SERVER).parts):
            continue
        if lines := tuple_asserts(path.read_text(encoding="utf-8")):
            found[path.relative_to(SERVER).as_posix()] = lines
    return found


def test_a_tuple_assert_is_found():
    source = """
def test_one(now):
    assert (now.round, now.heat, now.racing) == (7, 2, True)
    assert (
        now.round,
        now.heat,
    ) != (7, 2)
    assert now.round == 7
    assert now.pair == (7, 2)
    assert (now.round, now.heat) in {(7, 2), (7, 3)}
    assert (now.round, now.heat) not in []
    assert (1, 2) < (1, 3)

def timed(heats):
    assert (heats[0], heats[1]) == (1, 2)
"""
    assert tuple_asserts(source) == [3, 4, 15]


def test_no_test_file_adds_a_tuple_assert():
    found = [
        f"{name}: lines {lines}"
        for name, lines in counts().items()
        if len(lines) > KNOWN.get(name, 0)
    ]
    assert found == [], (
        "write one assert per field, so a failure names the field "
        f"(tests/test_tuple_asserts.py): {found}"
    )


def test_the_known_tuple_asserts_only_shrink():
    """A KNOWN count that is higher than its file holds is lowered, so the
    list only shrinks."""
    held = {name: len(lines) for name, lines in counts().items()}
    stale = {
        name: (count, held.get(name, 0))
        for name, count in KNOWN.items()
        if held.get(name, 0) < count
    }
    assert stale == {}, "lower each count to what its file holds (known, held)"
    assert len(KNOWN) <= KNOWN_FILES
    assert sum(KNOWN.values()) <= KNOWN_ASSERTS
