"""No code takes the first entry of a folder listing as it comes (#312 retro).

`next(folder.glob("*.html"))`, `os.listdir(folder)[0]` and the like return
whatever the file system lists first, and file systems disagree. #288's
RealTree test took each template folder's sample that way: ext4 listed
`base.html` first and tmpfs `placeholder.html`, so the test passed in the
hook and failed in a fresh worktree under /tmp. Take `min(...)` or
`sorted(...)[0]`, or go through every entry.

The scan reads one expression at a time. It finds a listing (`glob`,
`iglob`, `rglob`, `iterdir`, `listdir`, `scandir`), or one passed through a
call that keeps its order or has none (`list`, `tuple`, `iter`, `set`, `map`,
`filter`, `reversed`, `enumerate`) or through a list comprehension or a
generator, when it is passed to `next()`, subscripted, or unpacked into
several names. It misses a listing bound to a name and indexed later
(`names = os.listdir(d)` then `names[0]`) and a loop that breaks on its
first entry: review looks for those.
"""

import ast
import os
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
# The calls that list a folder in the file system's order.
LISTINGS = {"glob", "iglob", "rglob", "iterdir", "listdir", "scandir"}
SKIP = {".venv", "node_modules", "__pycache__"}


# The calls whose result keeps the order of a listing passed to them, or
# has none: list(folder.iterdir())[0] is the same pick.
WRAPPERS = {"list", "tuple", "iter", "set", "map", "filter", "reversed", "enumerate"}


def _listing(node: ast.AST | None) -> bool:
    """Whether `node` iterates in the order a listing gave."""
    if isinstance(node, ast.ListComp | ast.GeneratorExp):
        return _listing(node.generators[0].iter)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in LISTINGS:
        return True
    return name in WRAPPERS and any(_listing(arg) for arg in node.args)


def first_picks(source: str) -> list[int]:
    """The lines of `source` that take an entry of a listing by its place."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "next":
            if node.args and _listing(node.args[0]):
                found.append(node.lineno)
        elif isinstance(node, ast.Subscript) and _listing(node.value):
            found.append(node.lineno)
        elif isinstance(node, ast.Assign) and _listing(node.value):
            # x, *_ = folder.glob("*") takes entries by place; [only] = ...
            # takes the one entry there is, or fails.
            if any(
                isinstance(t, ast.Tuple | ast.List) and len(t.elts) > 1
                for t in node.targets
            ):
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
    picks = (
        "a = next(root.glob('*.html'))\n"
        "b = os.listdir(root)[0]\n"
        "c = list(root.iterdir())[-1]\n"
        "d = glob.glob('*.py')[0]\n"
        "e = next(os.scandir(root))\n"
        "f = next(glob.iglob('*'))\n"
        "g = tuple(root.rglob('*'))[0]\n"
        "h = next(iter(root.glob('*')))\n"
        "i = list(set(root.glob('*')))[0]\n"
        "j = list(map(str, root.iterdir()))[0]\n"
        "k = list(filter(None, root.glob('*')))[0]\n"
        "m = next(reversed(os.listdir(root)))\n"
        "n = list(enumerate(root.glob('*')))[0]\n"
        "o = [p for p in root.glob('*')][0]\n"
        "q = next(p for p in root.glob('*') if p.is_file())\n"
        "x, *_ = root.glob('*')\n"
        "[y, z] = os.listdir(root)\n"
    )
    assert first_picks(picks) == list(range(1, 18))
    misses = (
        "e = next(iter(x))\n"
        "f = min(root.glob('*.html'))\n"
        "g = sorted(root.rglob('*'))[0]\n"
        "h = next(p for p in sorted(root.glob('*')) if p.is_file())\n"
        "i = [p.name for p in sorted(root.glob('*'))][0]\n"
        "x, *_ = sorted(root.glob('*'))\n"
        "[only] = list(root.iterdir())\n"
        "[*every] = root.glob('*')\n"
        "names = list(root.glob('*'))\n"
        "count = len(list(root.iterdir()))\n"
        # What the scan misses, as the docstring says.
        "names = os.listdir(root)\n"
        "first = names[0]\n"
        "for p in root.glob('*'):\n"
        "    break\n"
    )
    assert first_picks(misses) == []


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
