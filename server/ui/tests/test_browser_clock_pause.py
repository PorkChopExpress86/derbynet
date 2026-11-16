"""A browser test installs Playwright's fake clock before the moment it
pauses at, never at that moment (#317 retro).

The installed clock runs from its install and syncs to real time within
about 100 ms, so `clock.install(time=T)` then `clock.pause_at(T)` fails
"Cannot fast-forward to the past" once the two calls are further apart than
that, as under load. #317's first head did this in `open_paused`, and its
DejaVu hand-off run failed there, which cost a second commit, a second
hand-off and both load runs. The browser tests now pause through
`helpers.pause_clock_at`, which installs a minute before the pause
(`test_the_clock_pauses_after_a_slow_install`, #348).

`KNOWN` holds the functions left to fix. An entry the sweep no longer
finds fails too: #348's fix merged with the scale-mph entry still in
place, where a new pause at the install in that function would have
passed (#348 retro).
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# What it reads past, left to review: a clock taken into a name first
# (`clock = page.clock`, then `clock.pause_at(T)`), and a time held in two
# names. A function nested in another is read on its own and as part of
# the outer one, so a pair inside it is named twice.

# (file name, function) of each pause at the install left to fix; add none.
KNOWN: set[tuple[str, str]] = set()


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


def time_of(call: ast.Call) -> str | None:
    """The time a clock call names, by its `time` keyword or its first
    argument, as an AST dump; None when it names none."""
    times = [k.value for k in call.keywords if k.arg == "time"] + call.args[:1]
    return ast.dump(times[0]) if times else None


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
                installs.add(time_of(call))
            elif call := clock_call(node, "pause_at"):
                pauses.append(call)
        found += [
            (function.name, call.lineno)
            for call in pauses
            if time_of(call) is not None and time_of(call) in installs
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


def by_keyword(page):
    page.clock.install(time=PAUSED_AT)
    page.clock.pause_at(time=PAUSED_AT)


def not_the_clock(page):
    page.clock.install(time=PAUSED_AT)
    page.mouse.pause_at(PAUSED_AT)


def another_keyword(page):
    page.clock.install(tz=PAUSED_AT)
    page.clock.pause_at(PAUSED_AT)


def no_times(page):
    page.clock.install()
    page.clock.pause_at()
"""
    assert pauses_at_the_install(source) == [
        ("open_paused", 4),
        ("by_position", 9),
        ("by_keyword", 28),
    ]


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


def sweep(folder: Path) -> list[tuple[str, str, int]]:
    """`(file name, function, line)` of each pause at the install time in
    the Python files under `folder`."""
    return [
        (path.name, function, line)
        for path in sorted(folder.rglob("*.py"))
        for function, line in pauses_at_the_install(path.read_text())
    ]


def unknown_pauses(folder: Path, known=KNOWN) -> list[str]:
    """`file:line in function` for each pause the sweep finds that `known`
    does not hold."""
    return [
        f"{name}:{line} in {function}"
        for name, function, line in sweep(folder)
        if (name, function) not in known
    ]


def stale_entries(folder: Path, known=KNOWN) -> list[str]:
    """`file in function` for each entry of `known` the sweep no longer
    finds, as `test_kiosk_scale_mph.py`'s after #348 fixed it."""
    found = {(name, function) for name, function, _ in sweep(folder)}
    return [f"{name} in {function}" for name, function in sorted(known - found)]


def test_the_sweep_finds_317s_open_paused(tmp_path):
    """Under a folder, and past a KNOWN entry."""
    (tmp_path / "kiosk").mkdir()
    (tmp_path / "kiosk" / "test_kiosk_results_by_racer.py").write_text(OPEN_PAUSED_317)
    (tmp_path / "test_kiosk_scale_mph.py").write_text(OPEN_PAUSED_317)
    known = {("test_kiosk_scale_mph.py", "open_paused")}
    assert unknown_pauses(tmp_path, known) == [
        "test_kiosk_results_by_racer.py:7 in open_paused"
    ]
    assert stale_entries(tmp_path, known) == []


# `open_paused` as #348's fix (1d97e769) left it.
OPEN_PAUSED_348 = """
def open_paused(page, event):
    run_heat(event)
    pause_clock_at(page, PAUSED_AT)
    open_kiosk(page, event, "kiosks/scale-mph.kiosk")
"""


def test_an_entry_the_sweep_no_longer_finds_is_stale(tmp_path):
    """#348 fixed `test_kiosk_scale_mph.py` and its entry stayed in KNOWN."""
    (tmp_path / "test_kiosk_scale_mph.py").write_text(OPEN_PAUSED_348)
    (tmp_path / "test_kiosk_results_by_racer.py").write_text(OPEN_PAUSED_317)
    known = {
        ("test_kiosk_scale_mph.py", "open_paused"),
        ("test_kiosk_results_by_racer.py", "open_paused"),
    }
    assert stale_entries(tmp_path, known) == ["test_kiosk_scale_mph.py in open_paused"]
    assert unknown_pauses(tmp_path, known) == []


def test_no_browser_test_pauses_at_its_install_time():
    found = unknown_pauses(BROWSER)
    assert found == [], (
        "install the clock before the moment it pauses at, through "
        f"helpers.pause_clock_at (#317, #348): {found}"
    )


def test_every_known_entry_is_still_found():
    stale = stale_entries(BROWSER)
    assert stale == [], (
        f"remove these from KNOWN, which the sweep no longer finds: {stale}"
    )
