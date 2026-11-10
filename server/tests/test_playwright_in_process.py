"""A test starts Playwright only through pytest-playwright's fixtures (#239 retro).

`tools/kiosk_screens.py` starts its own `sync_playwright()`. #239's tests
called its `main()` in the pytest process. In an xdist worker that had
already run a pytest-playwright test, Playwright's event loop was still
running, and the call failed with "Playwright Sync API inside the asyncio
loop": all four tests failed on both databases in CI run 42540562729. They
passed alone (`-n 0`), and source-checks and the pre-push hook passed them.
A test runs such a script as its own process instead, as
`ui/tests/test_kiosk_screens_tool.py` does.

This fails a test module or conftest under `server/` that names
`sync_playwright` or `async_playwright`, or names a function of a `server/`
module that starts one, directly or through another function of its module.
Calling the module's other functions, such as `kiosk_screens.watch()` with a
stand-in tab, stays allowed.

A test outside `ui/tests/browser/` that takes pytest-playwright's `page` (or
another fixture that starts Playwright) also starts it in the pytest process,
without the browser harness's conftest. #250's ellipsis test sat in
`ui/tests/` on `page`, and in a shared xdist worker the Django tests after it
failed with SynchronousOnlyOperation, 39 errors on Postgres (#250 retro).
This fails such a test or fixture, unless the name is its own parametrize
argument or a fixture its module defines.
"""

import ast
import functools
import importlib.util
import os
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
BROWSER_TESTS = SERVER / "ui" / "tests" / "browser"
SKIP = {".venv", "node_modules", "__pycache__", "var"}
START = {"sync_playwright", "async_playwright"}


def python_files():
    for root, dirs, files in os.walk(SERVER):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if name.endswith(".py"):
                yield Path(root, name)


def is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def names_in(node: ast.AST) -> set[str]:
    """Every bare name and attribute name the node uses."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def starters(source: str) -> set[str]:
    """The module's top-level functions that start Playwright, directly or
    by calling another such function of the module."""
    functions = {
        node.name: names_in(node)
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    found = {name for name, used in functions.items() if used & START}
    while grown := {n for n, used in functions.items() if used & found} - found:
        found |= grown
    return found


def in_process_starts(source: str, modules: dict[str, set[str]]) -> list[str]:
    """Each line of a test module that starts Playwright in the pytest
    process. `modules` maps a dotted module name to its starters."""
    tree = ast.parse(source)
    aliases: dict[str, set[str]] = {}  # a name bound to a module: its starters
    banned = set(START)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    bound = alias.asname or alias.name
                    aliases[bound] = modules[alias.name]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found += [f"{node.lineno}: {a.name}" for a in node.names if a.name in START]
            for alias in node.names:
                bound = alias.asname or alias.name
                full = f"{node.module}.{alias.name}"
                if full in modules:
                    aliases[bound] = modules[full]
                elif alias.name in modules.get(node.module, ()):
                    banned.add(bound)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in banned:
            found.append(f"{node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and (
            node.attr in START or node.attr in aliases.get(ast.unparse(node.value), ())
        ):
            found.append(f"{node.lineno}: {ast.unparse(node)}")
    return sorted(set(found), key=lambda line: int(line.split(":")[0]))


@functools.cache
def server_starters() -> dict[str, set[str]]:
    modules = {}
    for path in python_files():
        if is_test(path):
            continue
        source = path.read_text(encoding="utf-8")
        # A text match first: parsing every file, generator_tables.py's
        # 1.3 MB among them, made this module take 5 s instead of 0.5 s.
        if "_playwright" in source and (found := starters(source)):
            parts = path.relative_to(SERVER).with_suffix("").parts
            modules[".".join(parts)] = found
    return modules


TOOL = """
from playwright.sync_api import sync_playwright

def check(tab):
    return []

def run(base):
    with sync_playwright() as p:
        p.chromium.launch()

def main(argv=None):
    return run(argv[0])
"""


def test_a_function_that_starts_playwright_through_another_is_a_starter():
    assert starters(TOOL) == {"run", "main"}


def test_an_in_process_start_is_found():
    modules = {"tools.shots": {"run", "main"}}
    source = """
from playwright.sync_api import Error, sync_playwright
import playwright.sync_api as api
from tools import shots
from tools.shots import main as shots_main
import tools.shots

def test_one():
    shots.main(["x"])
    shots_main(["x"])
    tools.shots.run("x")
    api.sync_playwright()
    shots.check(None)
    raise Error("fine")
"""
    assert in_process_starts(source, modules) == [
        "2: sync_playwright",
        "9: shots.main",
        "10: shots_main",
        "11: tools.shots.run",
        "12: api.sync_playwright",
    ]


def test_a_subprocess_run_and_a_stand_in_tab_pass():
    modules = {"tools.shots": {"run", "main"}}
    source = """
import subprocess, sys
from tools import shots

def test_one(page):
    subprocess.run([sys.executable, "tools/shots.py", "x"])
    shots.check(page)
"""
    assert in_process_starts(source, modules) == []


def test_the_scan_finds_kiosk_screens():
    assert "main" in server_starters()["tools.kiosk_screens"]


def test_no_test_starts_playwright_in_process():
    modules = server_starters()
    # A module is named by its last part wherever a test imports it.
    signs = ("_playwright", *{name.rsplit(".", 1)[-1] for name in modules})
    found = {}
    for path in sorted(python_files()):
        if not is_test(path):
            continue
        source = path.read_text(encoding="utf-8")
        if any(sign in source for sign in signs) and (
            lines := in_process_starts(source, modules)
        ):
            found[str(path.relative_to(SERVER))] = lines
    assert found == {}, (
        "run the script as its own process (subprocess.run), or use "
        f"pytest-playwright's page and browser fixtures: {found}"
    )


def is_fixture(node: ast.AST) -> bool:
    return any("fixture" in names_in(d) for d in getattr(node, "decorator_list", []))


@functools.cache
def playwright_fixtures() -> frozenset[str]:
    """pytest-playwright's fixtures that start Playwright: `playwright` and
    each fixture that takes one of them, read from the plugin's source."""
    origin = importlib.util.find_spec("pytest_playwright.pytest_playwright").origin
    takes = {
        node.name: {a.arg for a in node.args.args}
        for node in ast.parse(Path(origin).read_text(encoding="utf-8")).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and is_fixture(node)
    }
    found = {"playwright"}
    while grown := {n for n, used in takes.items() if used & found} - found:
        found |= grown
    return frozenset(found)


def parametrized(node: ast.AST) -> set[str]:
    """The argument names a function's `parametrize` marks give it, written
    as `"a, b"` or `("a", "b")`."""
    found = set()
    for d in getattr(node, "decorator_list", []):
        if not (
            isinstance(d, ast.Call) and d.args and "parametrize" in names_in(d.func)
        ):
            continue
        arg = d.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found |= {name.strip() for name in arg.value.split(",")}
        elif isinstance(arg, (ast.Tuple, ast.List)):
            found |= {e.value for e in arg.elts if isinstance(e, ast.Constant)}
    return found


def playwright_fixture_uses(source: str, banned: frozenset[str]) -> list[str]:
    """Each test or fixture of a module that takes a fixture in `banned`, or
    names one in `usefixtures`."""
    tree = ast.parse(source)
    own = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and is_fixture(node)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and "usefixtures" in names_in(node.func):
            found += [
                f"{node.lineno}: usefixtures({a.value!r})"
                for a in node.args
                if isinstance(a, ast.Constant) and a.value in banned - own
            ]
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (node.name.startswith("test") or is_fixture(node)):
            continue
        args = {a.arg for a in node.args.args + node.args.kwonlyargs}
        for name in sorted(args & banned - own - parametrized(node)):
            found.append(f"{node.lineno}: {node.name}({name})")
    return sorted(found, key=lambda line: int(line.split(":")[0]))


def test_the_plugin_fixtures_that_start_playwright_are_read():
    assert {"playwright", "browser", "context", "page"} <= playwright_fixtures()
    assert "browser_context_args" in playwright_fixtures()
    assert "browser_type_launch_args" not in playwright_fixtures()


def test_a_test_on_page_outside_the_browser_folder_is_found():
    source = """
import pytest

@pytest.fixture
def tab(page):
    return page

def test_one(page):
    pass

@pytest.mark.usefixtures("context")
def test_two():
    pass

@pytest.mark.parametrize("page", ["/a/", "/b/"])
def test_three(client, page):
    pass

@pytest.mark.parametrize(("php", "page"), [("a.php", "/a/")])
def test_four(php, page):
    pass

def helper(page):
    pass
"""
    assert playwright_fixture_uses(source, playwright_fixtures()) == [
        "5: tab(page)",
        "8: test_one(page)",
        "11: usefixtures('context')",
    ]


def test_a_module_with_its_own_page_fixture_passes():
    source = """
import pytest

@pytest.fixture
def page():
    return "/a/"

def test_one(page):
    pass
"""
    assert playwright_fixture_uses(source, playwright_fixtures()) == []


def test_only_the_browser_folder_takes_playwrights_fixtures():
    banned = playwright_fixtures()
    found = {}
    for path in sorted(python_files()):
        if not is_test(path) or BROWSER_TESTS in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        if any(name in source for name in banned) and (
            lines := playwright_fixture_uses(source, banned)
        ):
            found[str(path.relative_to(SERVER))] = lines
    assert found == {}, (
        "move the test into ui/tests/browser/, whose conftest runs the "
        f"browser harness: {found}"
    )
