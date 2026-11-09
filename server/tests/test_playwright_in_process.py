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
"""

import ast
import functools
import os
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
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
