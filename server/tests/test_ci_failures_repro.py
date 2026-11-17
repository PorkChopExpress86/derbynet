"""plan/bin/ci-failures prints a load run that repeats one failed test on
origin/master. For a parametrized id, the printed command must select that
one case, `count` times, under pytest-repeat's renaming
(`[param]` -> `[param-N-COUNT]`): #374's lead narrowed the tool's bare
function to `[desktop-chromium-RaceCoordinator]` by hand, the run collected
0 items, and the 25-minute repeat ran only after a `-k` rewrite.
plan/tests run without pytest, so this collects the printed form here.
"""

import importlib.machinery
import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def ci_failures():
    loader = importlib.machinery.SourceFileLoader(
        "ci_failures_tool", str(ROOT / "plan" / "bin" / "ci-failures")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


STUB = """import pytest


@pytest.mark.parametrize("role", ["Coordinator", "Crew", "Coordinator-x"])
@pytest.mark.parametrize("size", ["desktop-chromium", "phone"])
def test_page(size, role):
    pass


def test_plain():
    pass
"""


def collected(tmp_path, test, count):
    """The node ids pytest collects from the printed command's arguments."""
    (tmp_path / "test_stub.py").write_text(STUB)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    command = ci_failures().repro(test, count)
    words = shlex.split(command)
    assert words[:2] == ["tools/pytest-under-load.sh", "--loops"], command
    # bash expands the braces, as the shell the lead pastes it into does.
    args = subprocess.run(
        ["bash", "-c", f"printf '%s\\n' {command.split(' ', 3)[3]}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")[:-1]
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
            "-c",
            str(tmp_path / "pytest.ini"),
            "-o",
            "addopts=",
            "--collect-only",
            "-q",
            *args[args.index("--count") :],
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return [line for line in run.stdout.splitlines() if "::" in line]


def test_a_parametrized_id_repeats_only_its_case(tmp_path):
    ids = collected(
        tmp_path, "test_stub.py::test_page[desktop-chromium-Coordinator]", 3
    )
    assert ids == [
        f"test_stub.py::test_page[desktop-chromium-Coordinator-{n}-3]"
        for n in (1, 2, 3)
    ]


def test_a_plain_id_repeats_count_times(tmp_path):
    ids = collected(tmp_path, "test_stub.py::test_plain", 3)
    assert ids == [f"test_stub.py::test_plain[{n}-3]" for n in (1, 2, 3)]
