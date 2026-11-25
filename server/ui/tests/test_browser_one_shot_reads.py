"""A browser test reads a box through a retrying wait, not `bounding_box()`
(#237 retro).

`bounding_box()` reads the layout once, and Playwright's `LocatorAssertions`
has no assertion on a box to retry instead, so a read right after a click
or a load can land before the layout settles. #237's review sent back two
such reads in `test_kiosk_thumbnails.py`; the fix waits with
`wait_for_function` (its `scaled()` helper). plan/AGENT-BRIEF.md (UI
pages) keeps a one-shot read only where one read is the point, and says so
in a comment: write `# one read:` and the reason on the line or the line
above.

An `evaluate` read is held to the same mark: one in a test function's
`assert` or assignment carries `# one read:` on its statement or in the
comment block right above it. #240 retro left `evaluate` to review, and
#494's review then found an unmarked one (#494 retro).
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# Reads that predate this check (#237 retro), not reviewed against the rule.
# Remove an entry when its test changes; never add one.
KNOWN = {
    ("test_checkin.py", "test_a_photo_over_plain_http_uploads_crops_and_shows"),
    ("test_shell.py", "test_set_up_gets_two_columns_on_a_desktop"),
    ("test_toasts.py", "test_a_toast_leaves_an_open_sheets_buttons_uncovered"),
}

# Test functions whose `evaluate` reads predate the mark's check (#494
# retro), not reviewed against the rule. Remove an entry when its test
# changes; never add one.
EVALUATE_KNOWN = {
    ("test_checkin.py", "test_a_long_name_keeps_the_phone_list_one_screen_wide"),
    ("test_checkin.py", "test_a_photo_over_plain_http_uploads_crops_and_shows"),
    ("test_checkin.py", "test_an_exif_6_photo_renders_upright"),
    ("test_components.py", "test_a_sheet_traps_the_page_and_returns_focus"),
    (
        "test_kiosk_banner.py",
        "test_a_title_that_grows_with_the_same_lineup_keeps_the_lanes_fitted",
    ),
    ("test_kiosk_names.py", "test_a_racer_name_cut_with_an_ellipsis_is_reported"),
    (
        "test_kiosk_names.py",
        "test_at_the_floor_a_name_taller_than_its_row_is_cut_at_its_foot",
    ),
    ("test_kiosk_names.py", "test_steady_polls_do_not_fit_again"),
    ("test_kiosk_names.py", "test_the_lane_font_stays_within_the_clamp"),
    ("test_kiosk_ondeck_labels.py", "test_each_heat_label_shows_whole"),
    ("test_kiosk_ondeck_labels.py", "test_each_master_schedule_label_shows_whole"),
    ("test_kiosk_ondeck_labels.py", "test_the_round_divider_shows_whole"),
    (
        "test_kiosk_results_by_racer.py",
        "test_axe_passes_dark_text_on_the_gradient_and_waits_on_the_clock",
    ),
    ("test_kiosk_results_by_racer.py", "test_every_rounds_cards_come_on_screen"),
    ("test_kiosk_results_by_racer.py", "test_the_clock_pauses_after_a_slow_install"),
    ("test_kiosk_scale_mph.py", "test_pause_clock_at_refuses_a_page_that_has_loaded"),
    ("test_kiosk_scale_mph.py", "test_the_car_waits_off_screen_between_drives"),
    ("test_kiosk_scale_mph.py", "test_the_clock_pauses_after_a_slow_install"),
    (
        "test_kiosk_slideshow.py",
        "test_a_slide_that_leaves_while_fading_in_fades_out_too",
    ),
    ("test_kiosk_thumbnails.py", "test_a_board_swap_while_a_thumbnail_loads"),
    ("test_kiosks.py", "test_a_held_enter_after_a_refusal_sends_nothing_more"),
    ("test_kiosks.py", "test_a_held_enter_in_a_sheet_form_submits_once"),
    ("test_kiosks.py", "test_a_held_enter_on_a_sheet_opener_opens_it_once"),
    ("test_kiosks.py", "test_a_press_during_a_taps_request_still_taps"),
    ("test_kiosks.py", "test_a_tap_follows_a_card_a_poll_swaps_while_bound_waits"),
    ("test_kiosks.py", "test_the_focus_comes_back_after_a_board_swap"),
    ("test_kiosks.py", "test_the_focus_comes_back_without_scrolling_the_page"),
    ("test_replay.py", "test_a_finish_during_a_clip_replays_again"),
    ("test_replay.py", "test_a_heat_replays_with_the_clip_window_and_uploads"),
    (
        "test_replay.py",
        "test_a_message_sent_before_the_websocket_opens_is_sent_once_it_does",
    ),
    ("test_replay.py", "test_cancel_stops_the_race_starts_timeout"),
    ("test_replay.py", "test_race_starts_names_the_clip_when_it_arrives"),
    ("test_replay.py", "test_race_starts_times_out_and_the_late_replay_is_ignored"),
    ("test_replay.py", "test_the_delay_counts_after_the_post_roll"),
    ("test_replay.py", "test_the_delay_right_click_and_resize"),
    ("test_replay.py", "test_the_page_cuts_a_clip_as_the_service_does"),
    ("test_settings_ice_servers.py", "test_the_field_takes_what_chromium_takes"),
    ("test_timer.py", "test_the_browser_timer_loads_every_file"),
    ("test_toasts.py", "test_a_toast_leaves_an_open_sheets_buttons_uncovered"),
    ("test_toasts.py", "test_sortablejs_loads_only_on_a_page_with_a_sortable_list"),
    ("test_vote.py", "test_a_status_poll_sent_before_the_award_opened_leaves_it_open"),
    (
        "test_vote.py",
        "test_closing_from_another_session_shows_closed_within_six_seconds",
    ),
}

MARK = "# one read:"


def unmarked_reads(source: str) -> list[tuple[str, int]]:
    """`(function, line)` of each `bounding_box()` call with no `# one read:`
    comment on its line or the line above."""
    lines = source.splitlines()
    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name if function is None else function
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "bounding_box"
            ):
                here = lines[child.lineno - 1]
                above = lines[child.lineno - 2] if child.lineno > 1 else ""
                if MARK not in here and MARK not in above:
                    found.append((name or "", child.lineno))
            visit(child, name)

    visit(ast.parse(source), None)
    return found


def _marked(lines: list[str], statement: ast.stmt) -> bool:
    """The mark is on the statement or in the comment block right above it."""
    span = range(statement.lineno - 1, statement.end_lineno)
    if any(MARK in lines[i] for i in span):
        return True
    for line in reversed(lines[: statement.lineno - 1]):
        if not line.lstrip().startswith("#"):
            return False
        if MARK in line:
            return True
    return False


def unmarked_evaluate_reads(source: str) -> list[tuple[str, int]]:
    """`(test, line)` of each `assert` or assignment in a test function that
    reads `evaluate()` with no `# one read:` mark."""
    lines = source.splitlines()
    found = []
    reads = (ast.Assert, ast.Assign, ast.AnnAssign, ast.AugAssign)
    for test in ast.parse(source).body:
        if not (
            isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef))
            and test.name.startswith("test_")
        ):
            continue
        for statement in ast.walk(test):
            if (
                isinstance(statement, reads)
                and any(
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "evaluate"
                    for call in ast.walk(statement)
                )
                and not _marked(lines, statement)
            ):
                found.append((test.name, statement.lineno))
    return found


def test_an_unmarked_read_is_found():
    source = """
def test_one(page):
    page.click("a")
    box = page.locator("b").bounding_box()
    # one read: the layout is fixed before the click
    fine = page.locator("c").bounding_box()
    also = page.locator("d").bounding_box()  # one read: nothing changed

    def helper():
        return page.locator("e").bounding_box()
"""
    assert unmarked_reads(source) == [("test_one", 4), ("test_one", 10)]


def test_every_bounding_box_read_is_marked():
    found = []
    for path in sorted(BROWSER.rglob("*.py")):
        for function, line in unmarked_reads(path.read_text()):
            if (path.name, function) not in KNOWN:
                found.append(f"{path.name}:{line} in {function}")
    assert found == [], (
        "wait for the box with a retrying wait_for_function (the scaled() "
        "helper in test_kiosk_thumbnails.py), or mark a read that is the point "
        f"with `{MARK} <why>` (plan/AGENT-BRIEF.md, UI pages): {found}"
    )


def test_each_known_read_is_still_there():
    """A KNOWN entry whose read is gone or marked is removed, so the list
    only shrinks."""
    left = set()
    for path in sorted(BROWSER.rglob("*.py")):
        for function, _ in unmarked_reads(path.read_text()):
            left.add((path.name, function))
    assert sorted(KNOWN - left) == []


def test_an_unmarked_evaluate_read_is_found():
    source = """
def test_one(page):
    page.evaluate("() => window.postMessage('go', '*')")
    assert page.evaluate("window.paused") is True
    # one read: the log holds the whole run,
    # written as it happened.
    log = page.evaluate("window.log")
    seen = page.evaluate(  # one read: nothing redraws it
        "window.seen"
    )
    count = len(page.evaluate("window.log"))

def helper(page):
    return page.evaluate("window.log") == []
"""
    assert unmarked_evaluate_reads(source) == [("test_one", 4), ("test_one", 11)]


def test_every_evaluate_read_is_marked():
    found = []
    for path in sorted(BROWSER.rglob("test_*.py")):
        for test, line in unmarked_evaluate_reads(path.read_text()):
            if (path.name, test) not in EVALUATE_KNOWN:
                found.append(f"{path.name}:{line} in {test}")
    assert found == [], (
        "read the page through a retrying expect or wait_for_function, or mark "
        f"a read that is the point with `{MARK} <why>` (plan/AGENT-BRIEF.md, "
        f"UI pages): {found}"
    )


def test_each_known_evaluate_read_is_still_there():
    """An EVALUATE_KNOWN entry whose reads are gone or marked is removed."""
    left = set()
    for path in sorted(BROWSER.rglob("test_*.py")):
        for test, _ in unmarked_evaluate_reads(path.read_text()):
            left.add((path.name, test))
    assert sorted(EVALUATE_KNOWN - left) == []
