"""A browser test installs Playwright's fake clock before the moment it
pauses at, never at that moment (#317 retro).

The installed clock runs from its install and syncs to real time within
about 100 ms, so `clock.install(time=T)` then `clock.pause_at(T)` fails
"Cannot fast-forward to the past" once the two calls are further apart than
that, as under load. #317's first head did this in `open_paused`, and its
DejaVu hand-off run failed there, which cost a second commit, a second
hand-off and both load runs. `test_kiosk_results_by_racer.py` now installs
at `INSTALLED_AT`, a minute before `PAUSED_AT`
(`test_the_clock_pauses_after_a_slow_install`).
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# #348 fixes this one; remove the entry when it merges, and add none.
KNOWN = {("test_kiosk_scale_mph.py", "open_paused")}


def clock_call(node: ast.AST, name: str) -> ast.Call | None:
    """`node` when it is a call of `<something>.clock.<name>(...)`."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "clock"
    ):
        return node
    return None


def pauses_at_the_install(source: str) -> list[tuple[str, int]]:
    """`(function, line)` of each `clock.pause_at(T)` in a function that
    also calls `clock.install(time=T)` with the same expression."""
    found = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        installs, pauses = set(), []
        for node in ast.walk(function):
            if call := clock_call(node, "install"):
                times = [k.value for k in call.keywords if k.arg == "time"]
                installs |= {ast.dump(t) for t in times + call.args[:1]}
            elif (call := clock_call(node, "pause_at")) and call.args:
                pauses.append(call)
        found += [
            (function.name, call.lineno)
            for call in pauses
            if ast.dump(call.args[0]) in installs
        ]
    return sorted(set(found), key=lambda f: f[1])


def test_a_pause_at_the_install_time_is_found():
    source = """
def open_paused(page):
    page.clock.install(time=PAUSED_AT)
    page.clock.pause_at(PAUSED_AT)


def by_position(page):
    page.clock.install(datetime(2026, 1, 1))
    page.clock.pause_at(datetime(2026, 1, 1))


def installed_early(page):
    page.clock.install(time=INSTALLED_AT)
    page.clock.pause_at(PAUSED_AT)


def another_object(page, other):
    page.clock.install(time=PAUSED_AT)
    other.pause_at(PAUSED_AT)


def installed_elsewhere(page):
    page.clock.pause_at(PAUSED_AT)
"""
    assert pauses_at_the_install(source) == [("open_paused", 4), ("by_position", 9)]


# `open_paused` as #317's first head (1b7c62ef) had it.
OPEN_PAUSED_317 = '''
def open_paused(page, event):
    """The kiosk ... on a clock paused before the page loads."""
    run_heat(event)
    run_heat(event)
    page.clock.install(time=PAUSED_AT)
    page.clock.pause_at(PAUSED_AT)
    open_kiosk(page, event, "kiosks/results-by-racer.kiosk")
'''


def unknown_pauses(folder: Path) -> list[str]:
    """`file:line in function` for each pause at the install time in the
    Python files under `folder` that KNOWN does not hold."""
    found = []
    for path in sorted(folder.rglob("*.py")):
        for function, line in pauses_at_the_install(path.read_text()):
            if (path.name, function) not in KNOWN:
                found.append(f"{path.name}:{line} in {function}")
    return found


def test_the_sweep_finds_317s_open_paused(tmp_path):
    """Under a folder, and past a KNOWN entry."""
    (tmp_path / "kiosk").mkdir()
    (tmp_path / "kiosk" / "test_kiosk_results_by_racer.py").write_text(OPEN_PAUSED_317)
    (tmp_path / "test_kiosk_scale_mph.py").write_text(OPEN_PAUSED_317)
    assert unknown_pauses(tmp_path) == [
        "test_kiosk_results_by_racer.py:7 in open_paused"
    ]


def test_no_browser_test_pauses_at_its_install_time():
    found = unknown_pauses(BROWSER)
    assert found == [], (
        "install the clock before the moment it pauses at (INSTALLED_AT in "
        f"test_kiosk_results_by_racer.py, #317): {found}"
    )
