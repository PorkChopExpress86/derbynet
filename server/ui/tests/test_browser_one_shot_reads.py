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
