"""`plan/bin/affected-tests` names the xunit-style setups and teardowns
pytest calls by their presence (`XUNIT`), so that D101 reads a change to
any of them as a hook's change and repeats the whole file (#336 review, M1:
its first list left out `setUpModule` and `tearDownModule`, which pytest
calls).

The names come from pytest itself: each candidate is defined alone in a
module, and alone in a test class, and the run records which pytest calls.
pytest calls only the first of `setUpModule` and `setup_module` that a
module defines, so each candidate gets a file of its own."""

import importlib.machinery
import importlib.util
import itertools
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PREFIXES = ("setup", "teardown", "setUp", "tearDown")
SUFFIXES = (
    "",
    "_module",
    "_function",
    "_class",
    "_method",
    "Module",
    "Function",
    "Class",
    "Method",
)
CANDIDATES = [p + s for p, s in itertools.product(PREFIXES, SUFFIXES)]


def load_tool():
    path = REPO / "plan" / "bin" / "affected-tests"
    loader = importlib.machinery.SourceFileLoader("affected_tests_tool", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def called_by_pytest(tmp_path):
    """The candidates pytest calls, as {(where, name)}."""
    log = tmp_path / "called.txt"
    for i, name in enumerate(CANDIDATES):
        (tmp_path / f"test_module_{i}.py").write_text(
            f"import os\n\n\ndef {name}(*args):\n"
            f"    open(os.environ['XUNIT_LOG'], 'a').write('module {name}\\n')\n\n\n"
            "def test_x():\n    pass\n"
        )
        (tmp_path / f"test_class_{i}.py").write_text(
            "import os\n\n\nclass TestC:\n"
            f"    def {name}(*args):\n"
            f"        open(os.environ['XUNIT_LOG'], 'a').write('class {name}\\n')\n\n"
            "    def test_x(self):\n        pass\n"
        )
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
            "-p",
            "no:xdist",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "XUNIT_LOG": str(log)},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{2 * len(CANDIDATES)} passed" in result.stdout, result.stdout
    return {tuple(line.split()) for line in log.read_text().splitlines()}


def test_xunit_is_what_pytest_calls(tmp_path):
    tool = load_tool()
    called = called_by_pytest(tmp_path)
    assert {name for _, name in called} == set(tool.XUNIT)
    # Each a module-level and a class-level name of its own, as pytest
    # reads them.
    assert ("module", "setUpModule") in called
    assert ("class", "setup_method") in called
    for name in tool.XUNIT:
        assert tool.PYTEST_READS.fullmatch(name)
    for name in set(CANDIDATES) - set(tool.XUNIT):
        assert not tool.PYTEST_READS.fullmatch(name), name
