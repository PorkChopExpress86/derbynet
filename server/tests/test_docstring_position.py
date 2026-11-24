"""A function's docstring is its first statement.

A string placed after the first statement is no docstring: Python drops it,
and `docstring_claims.py` never reads its claims. #470's
`test_the_report_marks_each_pair_affected_tests_maps` put its docstring
after `out, tmp_path = ...` at b1a560f1, and review found it (#470 retro).
"""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def is_string(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def late_docstrings(source: str) -> list[str]:
    """Each function whose second statement is a bare string and whose
    first is not, by name and the string's line."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if len(body) > 1 and not is_string(body[0]) and is_string(body[1]):
                found.append(f"{node.name}:{body[1].lineno}")
    return found


def test_every_docstring_comes_first():
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "server/*.py", "plan/*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    found = []
    for name in listed:
        path = ROOT / name
        if path.is_file():
            found += [f"{name}:{hit}" for hit in late_docstrings(path.read_text())]
    assert not found, "a docstring after the first statement:\n" + "\n".join(found)


def test_470s_late_docstring_is_named():
    source = (
        "def test_the_report(tmp_path):\n"
        '    out, tmp_path = tmp_path / "out", tmp_path / "repo"\n'
        '    """The report names each pair."""\n'
        "    assert out\n"
    )
    assert late_docstrings(source) == ["test_the_report:3"]


def test_a_docstring_first_is_not_named():
    assert late_docstrings('def f():\n    """Doc."""\n    "text"\n') == []
