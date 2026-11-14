"""No two top-level functions in `ui/` have the same code (#45 retro).

#45's review found `_int` copied into three views (one of them named
`_type`) and `car` in two modules, and moved them to `ui/responses.py`;
`_nocase` and `_round_name` stayed copied until this test. A copy drifts: the
playlist's `_int` refused a number past 32 bits and the groups page's did
not. This test compares each function's arguments, annotations, decorators
and body, with its name and docstring left out, so it finds a copy under any
name. Two helpers that differ by a line are past its reach; the review still
looks for them.

Put a shared helper in `ui/responses.py`, or in the service that owns its
rule.

A script in `tools/` imports what it shares with the server or the tests,
too: no function there may copy one anywhere in `server/`, tests included,
and no string constant of a line or more may either (#61's
`race_day_screens.py` copied `htmx_settled`, `dismiss_toasts` and `BOUND`
from the browser tests, with `page` renamed `tab`; review found them).
Arguments are compared by position, so a renamed argument is still a copy.
"""

import ast
import copy
from collections import defaultdict
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]


def _modules() -> list[Path]:
    return [
        path
        for path in sorted(SERVER.glob("ui/**/*.py"))
        if not {"tests", "migrations"} & set(path.relative_to(SERVER).parts)
    ]


def _server_modules() -> list[Path]:
    return [
        path
        for path in sorted(SERVER.glob("**/*.py"))
        if not {".venv", "migrations"} & set(path.relative_to(SERVER).parts)
    ]


class _Positional(ast.NodeTransformer):
    """Each argument renamed by its position, so `def f(page)` and
    `def f(tab)` with the same body compare equal."""

    def __init__(self, names: dict[str, str]):
        self.names = names

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self.names.get(node.arg, node.arg)
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.names.get(node.id, node.id)
        return node


def _shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    every = [*args.posonlyargs, *args.args, args.vararg, *args.kwonlyargs, args.kwarg]
    names = {a.arg: f"_{i}" for i, a in enumerate(every) if a is not None}
    node = _Positional(names).visit(copy.deepcopy(node))
    body = node.body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]
    return ast.dump(
        ast.FunctionDef(
            name="_",
            args=node.args,
            body=body,
            decorator_list=node.decorator_list,
            returns=node.returns,
            type_params=[],
        )
    )


def copies(sources: dict[str, str]) -> list[list[str]]:
    """Each group of top-level functions with the same code, as
    `file:line name`, from {filename: source}."""
    found = defaultdict(list)
    for filename, source in sources.items():
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[_shape(node)].append(f"{filename}:{node.lineno} {node.name}")
    return [group for group in found.values() if len(group) > 1]


def test_no_function_in_ui_is_a_copy_of_another():
    sources = {str(p.relative_to(SERVER)): p.read_text() for p in _modules()}
    assert copies(sources) == []


def _constants(sources: dict[str, str]) -> list[list[str]]:
    """Each group of top-level string constants with the same text and a
    newline in it, as `file:line NAME`."""
    found = defaultdict(list)
    for filename, source in sources.items():
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and "\n" in node.value.value
            ):
                names = " ".join(ast.unparse(t) for t in node.targets)
                found[node.value.value].append(f"{filename}:{node.lineno} {names}")
    return [group for group in found.values() if len(group) > 1]


def tool_copies(sources: dict[str, str]) -> list[list[str]]:
    """The groups of copied functions and string constants that hold a
    `tools/` module's, from {path relative to server/: source}."""
    return [
        group
        for group in copies(sources) + _constants(sources)
        if any(where.startswith("tools/") for where in group)
    ]


def test_no_tool_copies_a_function_or_constant_in_server():
    sources = {str(p.relative_to(SERVER)): p.read_text() for p in _server_modules()}
    assert tool_copies(sources) == []


INT_IN_AWARDS = '''
def _int(value) -> int | None:
    """A posted number."""
    try:
        return int(str(value).strip())
    except TypeError, ValueError:
        return None
'''

TYPE_IN_PRESENTER = """
def _type(value) -> int | None:
    try:
        return int(str(value).strip())
    except TypeError, ValueError:
        return None
"""

INT_IN_PLAYLIST = """
def _int(value) -> int | None:
    try:
        n = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return n if -(2**31) < n < 2**31 else None
"""


def test_it_finds_a_copy_under_another_name_and_docstring():
    """#45 before review: awards' `_int` and the presenter's `_type`."""
    assert copies({"awards.py": INT_IN_AWARDS, "presenter.py": TYPE_IN_PRESENTER}) == [
        ["awards.py:2 _int", "presenter.py:2 _type"]
    ]


def test_it_passes_two_helpers_that_differ():
    assert copies({"awards.py": INT_IN_AWARDS, "playlist.py": INT_IN_PLAYLIST}) == []


SETTLED_IN_CONFTEST = '''
def htmx_settled(page):
    """Wait until htmx has no request in flight."""
    page.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )
'''

BOUND_IN_TESTS = '''
BOUND = """root => [root, ...root.querySelectorAll('[hx-get], [hx-post]')]
  .every((e) => (e['htmx-internal-data'] || {}).initHash)"""
'''

SETTLED_IN_SCREENS = '''
def settled(tab):
    tab.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


BOUND = """root => [root, ...root.querySelectorAll('[hx-get], [hx-post]')]
  .every((e) => (e['htmx-internal-data'] || {}).initHash)"""


def wait(tab):
    tab.wait_for_timeout(500)


LABEL = "Start Scanning"
'''


def test_it_finds_a_tools_copy_of_a_test_helper_with_its_argument_renamed():
    """#61's first push: `race_day_screens.py` against the browser tests.
    The names carry no `.py`, so tests/test_php_cite_lines.py reads no cite."""
    assert tool_copies(
        {
            "browser/conftest": SETTLED_IN_CONFTEST,
            "browser/test_awards": BOUND_IN_TESTS,
            "tools/race_day_screens": SETTLED_IN_SCREENS,
        }
    ) == [
        [
            "browser/conftest:2 htmx_settled",
            "tools/race_day_screens:2 settled",
        ],
        [
            "browser/test_awards:2 BOUND",
            "tools/race_day_screens:8 BOUND",
        ],
    ]


def test_it_leaves_copies_between_test_files_to_the_review():
    assert (
        tool_copies(
            {
                "browser/conftest": SETTLED_IN_CONFTEST,
                "browser/test_kiosks": SETTLED_IN_CONFTEST,
            }
        )
        == []
    )


def test_it_tells_arguments_apart_by_position():
    swapped = "def f(a, b):\n    return a - b\n\n\ndef g(b, a):\n    return a - b\n"
    renamed = "def f(a, b):\n    return a - b\n\n\ndef g(x, y):\n    return x - y\n"
    assert copies({"m.py": swapped}) == []
    assert copies({"m.py": renamed}) == [["m.py:1 f", "m.py:5 g"]]
