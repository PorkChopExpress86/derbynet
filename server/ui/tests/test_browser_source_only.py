"""A test under ui/tests/browser/ takes a fixture: one that takes none
opens no page, so it checks only the source (#317 retro).

#317's first head added `test_kiosk_js_gives_a_request_up_after_15_s()`
to `test_kiosk_results_by_racer.py`, which read kiosk.js's text. A test
that reads only the source goes in a source check that
`tools/source-checks.sh` runs (plan/AGENT-BRIEF.md, UI pages), since a
browser file runs only under `-m browser` and its changes repeat under
load. The review replaced it with a behaviour test on the page
(`test_the_page_gives_a_poll_up_between_14_and_16_s`).
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# Tests that predate this check (#317 retro). Remove an entry when its test
# changes or moves; add none.
KNOWN = {
    ("test_harness.py", "test_every_type_the_harness_drops_has_a_held_request"),
    (
        "test_harness.py",
        "test_a_browser_file_named_after_one_outside_keeps_the_harness",
    ),
    ("test_harness.py", "test_a_browser_test_without_the_harness_fails_the_run"),
    ("test_harness.py", "test_the_collection_hook_runs_the_harness_check"),
    (
        "test_kiosk_thumbnails.py",
        "test_the_watch_outlasts_every_poll_but_the_check_in_list",
    ),
}


def fixtureless_tests(source: str) -> list[tuple[str, int]]:
    """`(test, line)` of each test function, top-level or a method of a
    `Test*` class, that takes no fixture."""
    found = []

    def tests(body, method):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
                if method and names[:1] == ["self"]:
                    names = names[1:]
                if node.name.startswith("test") and not names:
                    found.append((node.name, node.lineno))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                tests(node.body, True)

    tests(ast.parse(source).body, False)
    return found


def test_a_test_with_no_fixture_is_found():
    source = """
def test_reads_the_source():
    assert "GIVE_UP_MS" in KIOSK_JS.read_text()


def test_on_a_page(page):
    pass


def helper():
    pass


class TestKiosk:
    def test_method_with_none(self):
        pass

    def test_method_on_a_page(self, page):
        pass
"""
    assert fixtureless_tests(source) == [
        ("test_reads_the_source", 2),
        ("test_method_with_none", 15),
    ]


# The test as #317's first head (1b7c62ef) had it.
SOURCE_TEXT_317 = """
def test_kiosk_js_gives_a_request_up_after_15_s():
    assert "var GIVE_UP_MS = 15000;" in KIOSK_JS.read_text()
"""


def fixtureless(folder: Path) -> list[tuple[str, str, int]]:
    """`(file, test, line)` for each fixture-free test in the `test_*.py`
    files under `folder`."""
    return [
        (path.name, test, line)
        for path in sorted(folder.rglob("test_*.py"))
        for test, line in fixtureless_tests(path.read_text())
    ]


def test_the_sweep_finds_317s_source_text_test(tmp_path):
    """Under a folder, in a `test_*.py` file only."""
    (tmp_path / "kiosk").mkdir()
    (tmp_path / "kiosk" / "test_kiosk_results_by_racer.py").write_text(SOURCE_TEXT_317)
    (tmp_path / "helpers.py").write_text(SOURCE_TEXT_317)
    assert fixtureless(tmp_path) == [
        (
            "test_kiosk_results_by_racer.py",
            "test_kiosk_js_gives_a_request_up_after_15_s",
            2,
        )
    ]


def test_every_browser_test_takes_a_fixture():
    found = [
        f"{name}:{line} {test}"
        for name, test, line in fixtureless(BROWSER)
        if (name, test) not in KNOWN
    ]
    assert found == [], (
        "a browser test with no fixture opens no page and checks only the "
        "source: test the page's behaviour, or move it to a source check "
        f"listed in tools/source-checks.sh (plan/AGENT-BRIEF.md): {found}"
    )


def test_each_known_test_is_still_there():
    """A KNOWN entry whose test is gone or takes a fixture now is removed,
    so the list only shrinks."""
    left = {(name, test) for name, test, _ in fixtureless(BROWSER)}
    assert sorted(KNOWN - left) == []
