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

An `evaluate` read is held to the same mark: a statement of a test function
whose expression reads the page through `evaluate`, `evaluate_all`,
`eval_on_selector` or `eval_on_selector_all` (an `assert`, an assignment,
an `if`, a `for`, a `return`, a call's argument) carries `# one read:` on
it or in the comment block right above it. A call alone as its statement
is an action. #240 retro left `evaluate` to review, and #494's review then
found an unmarked one (#494 retro).
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

# Each test function whose unmarked `evaluate` reads predate the mark's
# check (#494 retro), with how many it holds; not reviewed against the rule.
# Lower a count or remove an entry when its test changes; never add one or
# raise one. KNOWN_TESTS and KNOWN_READS are this list's size the day it was
# written, so an entry added fails (#512 review).
EVALUATE_KNOWN = {
    ("test_checkin.py", "test_a_long_name_keeps_the_phone_list_one_screen_wide"): 1,
    ("test_checkin.py", "test_a_photo_over_plain_http_uploads_crops_and_shows"): 1,
    ("test_checkin.py", "test_an_exif_6_photo_renders_upright"): 1,
    ("test_checkin.py", "test_sort_by_name_partition_car_and_class"): 1,
    ("test_components.py", "test_a_sheet_traps_the_page_and_returns_focus"): 1,
    (
        "test_kiosk_banner.py",
        "test_a_title_that_grows_with_the_same_lineup_keeps_the_lanes_fitted",
    ): 1,
    ("test_kiosk_names.py", "test_a_racer_name_cut_with_an_ellipsis_is_reported"): 1,
    (
        "test_kiosk_names.py",
        "test_at_the_floor_a_name_taller_than_its_row_is_cut_at_its_foot",
    ): 1,
    ("test_kiosk_names.py", "test_steady_polls_do_not_fit_again"): 2,
    ("test_kiosk_names.py", "test_the_lane_font_stays_within_the_clamp"): 2,
    ("test_kiosk_ondeck_labels.py", "test_each_heat_label_shows_whole"): 2,
    ("test_kiosk_ondeck_labels.py", "test_each_master_schedule_label_shows_whole"): 2,
    ("test_kiosk_ondeck_labels.py", "test_the_round_divider_shows_whole"): 1,
    (
        "test_kiosk_results_by_racer.py",
        "test_axe_passes_dark_text_on_the_gradient_and_waits_on_the_clock",
    ): 3,
    ("test_kiosk_results_by_racer.py", "test_every_rounds_cards_come_on_screen"): 4,
    ("test_kiosk_results_by_racer.py", "test_the_clock_pauses_after_a_slow_install"): 1,
    (
        "test_kiosk_scale_mph.py",
        "test_pause_clock_at_refuses_a_page_that_has_loaded",
    ): 1,
    ("test_kiosk_scale_mph.py", "test_the_car_waits_off_screen_between_drives"): 3,
    ("test_kiosk_scale_mph.py", "test_the_clock_pauses_after_a_slow_install"): 1,
    (
        "test_kiosk_slideshow.py",
        "test_a_slide_that_leaves_while_fading_in_fades_out_too",
    ): 2,
    ("test_kiosk_thumbnails.py", "test_a_board_swap_while_a_thumbnail_loads"): 1,
    ("test_kiosks.py", "test_a_held_enter_after_a_refusal_sends_nothing_more"): 2,
    ("test_kiosks.py", "test_a_held_enter_in_a_sheet_form_submits_once"): 1,
    ("test_kiosks.py", "test_a_held_enter_on_a_sheet_opener_opens_it_once"): 2,
    ("test_kiosks.py", "test_a_held_tab_on_the_board_still_repeats"): 1,
    ("test_kiosks.py", "test_a_press_during_a_taps_request_still_taps"): 2,
    (
        "test_kiosks.py",
        "test_a_space_press_whose_focus_moves_off_applies_the_held_board",
    ): 1,
    ("test_kiosks.py", "test_a_tap_follows_a_card_a_poll_swaps_while_bound_waits"): 2,
    ("test_kiosks.py", "test_the_focus_comes_back_after_a_board_swap"): 3,
    ("test_kiosks.py", "test_the_focus_comes_back_without_scrolling_the_page"): 1,
    ("test_photos.py", "test_sort_search_and_the_repository_switch"): 1,
    ("test_replay.py", "test_a_finish_during_a_clip_replays_again"): 3,
    ("test_replay.py", "test_a_heat_replays_with_the_clip_window_and_uploads"): 5,
    (
        "test_replay.py",
        "test_a_message_sent_before_the_websocket_opens_is_sent_once_it_does",
    ): 1,
    ("test_replay.py", "test_cancel_stops_the_race_starts_timeout"): 1,
    ("test_replay.py", "test_race_starts_names_the_clip_when_it_arrives"): 3,
    ("test_replay.py", "test_race_starts_times_out_and_the_late_replay_is_ignored"): 4,
    ("test_replay.py", "test_the_delay_counts_after_the_post_roll"): 1,
    ("test_replay.py", "test_the_delay_right_click_and_resize"): 3,
    ("test_replay.py", "test_the_page_cuts_a_clip_as_the_service_does"): 2,
    ("test_results.py", "test_d37_a_closed_lanes_times_show_dropped"): 1,
    ("test_settings_ice_servers.py", "test_the_field_takes_what_chromium_takes"): 3,
    ("test_timer.py", "test_the_browser_timer_loads_every_file"): 2,
    ("test_timer.py", "test_the_log_tail_backs_off"): 1,
    ("test_toasts.py", "test_a_toast_leaves_an_open_sheets_buttons_uncovered"): 1,
    ("test_toasts.py", "test_sortablejs_loads_only_on_a_page_with_a_sortable_list"): 1,
    (
        "test_vote.py",
        "test_a_status_poll_sent_before_the_award_opened_leaves_it_open",
    ): 1,
    (
        "test_vote.py",
        "test_closing_from_another_session_shows_closed_within_six_seconds",
    ): 2,
}
KNOWN_TESTS = 48
KNOWN_READS = 84

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


def _marked(lines: list[str], first: int, last: int) -> bool:
    """The mark is on lines `first` to `last` or in the comment block right
    above them."""
    if any(MARK in line for line in lines[first - 1 : last]):
        return True
    for line in reversed(lines[: first - 1]):
        if not line.lstrip().startswith("#"):
            return False
        if MARK in line:
            return True
    return False


# The calls that read the page in its JavaScript (#512 review).
EVALUATES = {"evaluate", "evaluate_all", "eval_on_selector", "eval_on_selector_all"}


def _header(statement: ast.stmt):
    """The statement's own expressions, not those of the statements in its
    body: an `if`'s test, a `for`'s iterable, a `with`'s items, a value."""
    for value in ast.iter_fields(statement):
        for node in value[1] if isinstance(value[1], list) else [value[1]]:
            if isinstance(node, ast.withitem):
                yield node.context_expr
            elif isinstance(node, ast.expr):
                yield node


def _reads(node: ast.expr) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in EVALUATES
        for call in ast.walk(node)
    )


def unmarked_evaluate_reads(source: str) -> list[tuple[str, int]]:
    """`(test, line)` of each statement in a test function whose expression
    reads the page through an `evaluate` call with no `# one read:` mark. A
    call alone as its statement, its value dropped, is an action, no read."""
    lines = source.splitlines()
    found = []
    for test in ast.parse(source).body:
        if not (
            isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef))
            and test.name.startswith("test_")
        ):
            continue
        for statement in (n for top in test.body for n in ast.walk(top)):
            if not isinstance(statement, ast.stmt):
                continue
            if isinstance(statement, ast.Expr) and isinstance(
                statement.value, ast.Call
            ):
                call = statement.value
                header = [call.func, *call.args, *(k.value for k in call.keywords)]
            else:
                header = list(_header(statement))
            if not any(_reads(node) for node in header):
                continue
            end = max(node.end_lineno for node in header)
            if not _marked(lines, statement.lineno, end):
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
    if page.evaluate("window.ready"):
        page.click("a")
    for row in page.eval_on_selector_all("tr", "rs => rs.length"):
        pass
    print(page.locator("p").evaluate_all("ps => ps.length"))
    with open(page.evaluate("window.name")):
        page.evaluate("window.list").index(3)
    return page.eval_on_selector("p", "p => p.id")

def helper(page):
    return page.evaluate("window.log") == []
"""
    assert unmarked_evaluate_reads(source) == [
        ("test_one", 4),
        ("test_one", 11),
        ("test_one", 12),
        ("test_one", 14),
        ("test_one", 16),
        ("test_one", 17),
        ("test_one", 18),
        ("test_one", 19),
    ]


def _evaluate_reads() -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for path in sorted(BROWSER.rglob("test_*.py")):
        for test, _ in unmarked_evaluate_reads(path.read_text()):
            counts[path.name, test] = counts.get((path.name, test), 0) + 1
    return counts


def test_every_evaluate_read_is_marked():
    found = [
        f"{file} {test}: {n} unmarked, {EVALUATE_KNOWN.get((file, test), 0)} known"
        for (file, test), n in _evaluate_reads().items()
        if n > EVALUATE_KNOWN.get((file, test), 0)
    ]
    assert found == [], (
        "read the page through a retrying expect or wait_for_function, or mark "
        f"a read that is the point with `{MARK} <why>` (plan/AGENT-BRIEF.md, "
        f"UI pages): {found}"
    )


def test_each_known_evaluate_read_is_still_there():
    """An EVALUATE_KNOWN count above its test's unmarked reads is lowered,
    and an entry with none left is removed, so the list only shrinks."""
    counts = _evaluate_reads()
    stale = {k: n for k, n in EVALUATE_KNOWN.items() if counts.get(k, 0) < n}
    assert stale == {}


def test_the_known_evaluate_reads_only_shrink():
    """An entry added for a new test, unmarked reads and all, fails here."""
    assert len(EVALUATE_KNOWN) <= KNOWN_TESTS
    assert sum(EVALUATE_KNOWN.values()) <= KNOWN_READS
