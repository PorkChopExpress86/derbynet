"""No code takes the first entry of a folder listing as it comes (#312 retro).

`next(folder.glob("*.html"))`, `os.listdir(folder)[0]` and the like return
whatever the file system lists first, and file systems disagree. #288's
RealTree test took each template folder's sample that way: ext4 listed
`base.html` first and tmpfs `placeholder.html`, so the test passed in the
hook and failed in a fresh worktree under /tmp. Take `min(...)` or
`sorted(...)[0]`, or go through every entry.
"""

import ast
import os
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
# The calls that list a folder in the file system's order.
LISTINGS = {"glob", "iglob", "rglob", "iterdir", "listdir", "scandir"}
SKIP = {".venv", "node_modules", "__pycache__"}


def _listing(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in LISTINGS:
        return True
    # list(folder.iterdir())[0] is the same pick.
    return name in {"list", "tuple"} and len(node.args) == 1 and _listing(node.args[0])


def first_picks(source: str) -> list[int]:
    """The lines of `source` that take an entry of a listing by its place."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "next":
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.GeneratorExp):
                arg = arg.generators[0].iter
            if _listing(arg):
                found.append(node.lineno)
        elif isinstance(node, ast.Subscript) and _listing(node.value):
            found.append(node.lineno)
    return sorted(found)


def _python_files() -> list[Path]:
    files = []
    for top in (SERVER, REPO / "plan"):
        for folder, dirs, names in os.walk(top):
            dirs[:] = [d for d in dirs if d not in SKIP]
            files += [Path(folder) / n for n in names if n.endswith(".py")]
    files += [
        p
        for p in (REPO / "plan" / "bin").iterdir()
        if p.is_file()
        and p.read_text(errors="replace").startswith("#!/usr/bin/env python3")
    ]
    return sorted(files)


def test_the_scan_finds_each_pick():
    source = (
        "a = next(root.glob('*.html'))\n"
        "b = os.listdir(root)[0]\n"
        "c = list(root.iterdir())[-1]\n"
        "d = glob.glob('*.py')[0]\n"
        "e = next(iter(x))\n"
        "f = min(root.glob('*.html'))\n"
        "g = sorted(root.rglob('*'))[0]\n"
        "h = next(p for p in sorted(root.glob('*')) if p.is_file())\n"
        "i = next(p for p in root.glob('*') if p.is_file())\n"
    )
    assert first_picks(source) == [1, 2, 3, 4, 9]


def test_the_scan_reads_plan_bin():
    names = {p.name for p in _python_files()}
    assert {
        "affected-tests",
        "test_affected_tests.py",
        "test_listing_order.py",
    } <= names


def test_no_code_takes_the_first_entry_of_a_listing():
    found = [
        f"{path.relative_to(REPO)}:{line}"
        for path in _python_files()
        for line in first_picks(path.read_text(errors="replace"))
    ]
    assert found == [], "take min(...) or sorted(...)[0] of the listing"
