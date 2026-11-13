"""A browser test drops a 404 with `page_errors.allow_missing(url)` (#206
retro).

Chromium's console error for a 404 names no URL, so a `page_errors.allow`
pattern that matches it drops the 404 of every URL the page loads. #206's
bad vote link test allowed `status of 404 \\(Not Found\\)$`, which
plan/AGENT-BRIEF.md's rule for allowances forbids, until review sent it to
`allow_missing`, which matches the 404's URL. This fails each `allow` whose
pattern, a string or a module constant, matches that console error.
"""

import ast
import re
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# A test that clears every error once its own assert has read them.
ALLOWED = {
    ("test_dashboard.py", "test_a_poll_that_fails_otherwise_still_fails_the_test")
}


def missing_message() -> str:
    """The console error `PageErrors.add` gets for a 404, from
    `PageErrors.MISSING` in the browser conftest."""
    tree = ast.parse((BROWSER / "conftest.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and [getattr(t, "id", None) for t in node.targets] == ["MISSING"]
            and isinstance(node.value, ast.Constant)
        ):
            return f"console: {node.value.value} (Not Found)"
    raise AssertionError("conftest.py has no PageErrors.MISSING")


def broad_404s(source: str, name: str = "") -> list[str]:
    """`line: pattern` for each `.allow(pattern)` in `source` that drops a
    404 of any URL, outside the tests `ALLOWED` names."""
    tree = ast.parse(source)
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    skip = {
        id(node)
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef) and (name, func.name) in ALLOWED
        for node in ast.walk(func)
    }
    message = missing_message()
    found = []
    for node in ast.walk(tree):
        if (
            id(node) in skip
            or not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "allow"
            or not node.args
        ):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            pattern = arg.value
        elif isinstance(arg, ast.Name) and arg.id in constants:
            pattern = constants[arg.id]
        else:
            continue
        if re.search(pattern, message):
            found.append(f"{node.lineno}: {pattern}")
    return found


def test_no_allowance_drops_every_404():
    found = []
    for path in sorted(BROWSER.glob("test_*.py")):
        found += [
            f"{path.name}:{hit}"
            for hit in broad_404s(path.read_text(encoding="utf-8"), path.name)
        ]
    assert found == [], "use page_errors.allow_missing(<the 404's URL>)"


def test_a_404_allowance_by_status_is_found():
    source = (
        "BAD = r'status of 404'\n"
        "def test_a(page_errors):\n"
        "    page_errors.allow(r'status of 404 \\(Not Found\\)$')\n"
        "    page_errors.allow(BAD)\n"
        "    page_errors.allow(r'/held-request net::ERR_ABORTED$')\n"
        "    page_errors.allow_missing(r'/vote/bad/$')\n"
    )

    assert broad_404s(source) == [
        r"3: status of 404 \(Not Found\)$",
        "4: status of 404",
    ]
