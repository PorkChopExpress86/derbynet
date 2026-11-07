"""No test or fixture takes a fixture only pytest-xdist provides (#220 retro).

`-p no:xdist` removes xdist's `worker_id` and `testrun_uid` fixtures. The
browser conftest's `_fresh_css` took `worker_id`, so #220's run of one file
"alone, no xdist" errored all 20 tests at setup. A fixture reads
`config.workerinput` instead, which only an xdist worker has, so a run with
`-n 0` and one with `-p no:xdist` both work.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
XDIST_ONLY = {"worker_id", "testrun_uid"}


def xdist_fixture_uses(source: str) -> list[str]:
    return [
        f"{node.lineno}: {node.name}({arg.arg})"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for arg in node.args.args + node.args.kwonlyargs
        if arg.arg in XDIST_ONLY
    ]


def test_the_check_finds_an_xdist_fixture():
    source = "def _fresh_css(tmp_path_factory, worker_id):\n    pass\n"
    assert xdist_fixture_uses(source) == ["1: _fresh_css(worker_id)"]
    assert xdist_fixture_uses("def f(config):\n    pass\n") == []


def test_no_test_or_fixture_needs_xdist():
    found = [
        f"{path.relative_to(SERVER)}:{use}"
        for path in sorted(SERVER.rglob("*.py"))
        if ".venv" not in path.parts and "node_modules" not in path.parts
        for use in xdist_fixture_uses(path.read_text())
    ]
    assert found == [], found
