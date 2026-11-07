"""A browser test answers a routed request with a body or a 204 (#224 retro).

Chromium ends an empty 200 that the page never reads with `net::ERR_ABORTED`
while the page is still open (`test_an_unread_empty_200_is_an_abort`). The
settle tests answered their poll with `route.fulfill(body="")` and failed
2 to 3 load runs in 50 at teardown (#224). That was checked for an unread
`fetch` only, so this asks every empty 200 for a body or `status=204`.

It reads the call's own keywords: a body passed as a variable is not seen.
"""

import ast
from pathlib import Path

BROWSER = Path(__file__).resolve().parent / "browser"

# The test that shows the abort answers an empty 200 on purpose.
ALLOWED = {("test_harness.py", "test_an_unread_empty_200_is_an_abort")}


def _is_empty(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value in ("", b""))


def _is_200(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value == 200)


def empty_200s(source: str, allowed: set[str] = frozenset()) -> list[str]:
    """Each `.fulfill(...)` with no `json`, `path` or `response`, a status
    of 200 or none, and a body of `""` or none, outside the functions named
    in `allowed`."""
    tree = ast.parse(source)
    skip = {
        id(node)
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef) and func.name in allowed
        for node in ast.walk(func)
    }
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fulfill"
            and id(node) not in skip
        ):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        if node.args or None in keywords:
            continue  # positional or **kwargs: not read here
        if {"json", "path", "response"} & keywords.keys():
            continue
        if _is_200(keywords.get("status")) and _is_empty(keywords.get("body")):
            found.append(f"{node.lineno}: {ast.unparse(node)}")
    return found


def test_an_empty_200_is_found():
    source = """
route.fulfill(body="")
route.fulfill()
route.fulfill(status=200, body=b"")
route.fulfill(status=204)
route.fulfill(status=500, body="")
route.fulfill(body="<title>Timer</title>")
route.fulfill(json={})
route.fulfill(path="x.png")
route.fulfill(response=response)
route.fulfill(body=text)

def shows_the_abort(route):
    route.fulfill(body="")
"""
    assert empty_200s(source, {"shows_the_abort"}) == [
        "2: route.fulfill(body='')",
        "3: route.fulfill()",
        "4: route.fulfill(status=200, body=b'')",
    ]


def test_every_browser_test_answers_with_a_body_or_a_204():
    found = {
        path.name: calls
        for path in sorted(BROWSER.glob("test_*.py"))
        if (
            calls := empty_200s(
                path.read_text(encoding="utf-8"),
                {name for file, name in ALLOWED if file == path.name},
            )
        )
    }
    assert found == {}, (
        "an unread empty 200 aborts (#224); answer status=204 or give it "
        f"a body: {found}"
    )
