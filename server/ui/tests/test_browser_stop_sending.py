"""Only the browser harness stops a page sending (#509 retro).

`stop_sending()` and `STOP_SENDING` replace the page's `window.fetch` with a
fetch that never settles, and the settle sends its barrier on
`window.fetch`. A test that stopped its own page first left the barrier
unsent, and its settle waited out `InFlight.TIMEOUT_MS`: #509's kiosk stop
reused `stop_sending()`, and its tests timed out with no cause named.
`stop_timers()` stops a page's polls and leaves its fetch.
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"
NAMES = {"stop_sending", "STOP_SENDING"}


def uses(source: str) -> list[int]:
    """Lines that name `stop_sending` or `STOP_SENDING`, as a name, an
    attribute (`conftest.stop_sending`) or an import."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            (isinstance(node, ast.Name) and node.id in NAMES)
            or (isinstance(node, ast.Attribute) and node.attr in NAMES)
            or (
                isinstance(node, ast.ImportFrom)
                and any(alias.name in NAMES for alias in node.names)
            )
        ):
            found.append(node.lineno)
    return sorted(set(found))


def test_a_use_outside_the_harness_is_found():
    source = """
from ui.tests.browser.conftest import stop_sending
from ui.tests.browser import conftest

def stop_the_kiosk(page):
    stop_sending(page)
    conftest.stop_sending(page)
    page.evaluate(conftest.STOP_SENDING, None)
    conftest.stop_timers(page)
"""
    assert uses(source) == [2, 6, 7, 8]


def test_only_the_harness_stops_a_page_sending():
    found = [
        f"{path.relative_to(BROWSER)}:{line}"
        for path in sorted(BROWSER.rglob("*.py"))
        if path.name != "conftest.py"
        for line in uses(path.read_text())
    ]
    assert found == [], (
        "stop_sending() replaces window.fetch, which the settle's barrier "
        "needs, so the settle waits out its timeout; stop a page's polls "
        f"with stop_timers(page) instead: {found}"
    )
