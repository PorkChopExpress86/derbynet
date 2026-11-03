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
"""

import ast
from collections import defaultdict
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]


def _modules() -> list[Path]:
    return [
        path
        for path in sorted(SERVER.glob("ui/**/*.py"))
        if not {"tests", "migrations"} & set(path.relative_to(SERVER).parts)
    ]


def _shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
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
