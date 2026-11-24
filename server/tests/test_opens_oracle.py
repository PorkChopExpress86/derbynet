"""`tools/opens_oracle.py`, #470's runtime oracle for affected-tests' testing/
rules, run as a pytest plugin over a stand-in repository: each kind of read
it records reaches the report as a pair with the test that made it."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"

spec = importlib.util.spec_from_file_location("opens_oracle", TOOLS / "opens_oracle.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)

STAND_IN = {
    "testing/data/roster.csv": "a,b\n",
    "testing/data/thread.csv": "",
    "testing/data/upload.csv": "",
    "testing/data/upload2.csv": "",
    "testing/data/relative.csv": "",
    "server/testing-notes/a.txt": "",
    "testing/shell.sh": "",
    "testing/alone.sh": "#!/bin/sh\n",
    "testing/data/faces/one.jpg": "",
    "testing/run.sh": "#!/bin/bash\n",
    "testing/never.txt": "",
    "server/pytest.ini": "[pytest]\n",
    "server/helpers/__init__.py": "",
    "server/helpers/rows.py": "from helpers.util import read\n\nSCRIPT = read()\n",
    "server/helpers/util.py": (
        "from pathlib import Path\n\n\n"
        "def read():\n"
        "    testing = Path(__file__).resolve().parents[2] / 'testing'\n"
        "    return (testing / 'run.sh').read_text()\n"
    ),
    "server/test_util.py": "def test_c():\n    from helpers import util\n",
    "server/test_reads.py": (
        "import asyncio\n"
        "import os\n"
        "import subprocess\n"
        "import threading\n"
        "from pathlib import Path\n\n"
        "TESTING = Path(__file__).resolve().parents[1] / 'testing'\n\n\n"
        "def test_opens():\n"
        "    (TESTING / 'data' / 'roster.csv').read_text()\n"
        "    (TESTING / 'data' / 'untracked.csv').read_text()\n\n\n"
        "def test_lists():\n"
        "    sorted((TESTING / 'data' / 'faces').iterdir())\n\n\n"
        "def test_runs():\n"
        "    subprocess.run(['true', str(TESTING / 'run.sh')], check=True)\n"
        "    subprocess.run(f'true {TESTING}/shell.sh', shell=True, check=True)\n"
        "    subprocess.run(str(TESTING / 'alone.sh'), check=True)\n\n\n"
        "def test_reads_in_a_thread():\n"
        "    path = TESTING / 'data' / 'thread.csv'\n"
        "    read = threading.Thread(target=path.read_text)\n"
        "    read.start()\n"
        "    read.join()\n\n\n"
        "def test_uploads():\n"
        "    from playwright._impl import _frame\n"
        "    try:\n"
        "        data = TESTING / 'data'\n"
        "        files = [data / 'upload.csv', data / 'untracked.csv']\n"
        "        asyncio.run(_frame.convert_input_files(files, None))\n"
        "    except Exception:\n"
        "        pass  # the driver's half needs a browser context\n"
        "    try:\n"
        "        one = str(TESTING / 'data' / 'upload2.csv')\n"
        "        asyncio.run(_frame.convert_input_files(one, None))\n"
        "    except Exception:\n"
        "        pass\n\n\n"
        "def test_lists_elsewhere():\n"
        "    sorted(Path(os.getcwd()).iterdir())\n"
        "    sorted(Path('testing-notes').iterdir())\n\n\n"
        "def test_opens_relative():\n"
        "    here = os.getcwd()\n"
        "    os.chdir(TESTING / 'data')\n"
        "    try:\n"
        "        Path('relative.csv').read_text()\n"
        "    finally:\n"
        "        os.chdir(here)\n"
    ),
    "server/test_imports.py": "def test_a():\n    import helpers.rows\n",
    "server/test_top.py": "from helpers import rows\n\n\ndef test_b():\n    pass\n",
}


def test_each_read_maps_to_the_test_that_made_it(tmp_path):
    """An open of a tracked file, in the test's thread or another, by a
    relative path from a cwd inside testing/ too, a
    listing, a command line, as a list, a shell string or a lone path, and
    a tracked file handed to Playwright's set_input_files, in a list or
    alone, count for the test running; a read at a module's top level during collection
    counts for each test file that imports the module, by the oracle's own
    graph, inside a function too, and not for a file that imports only the
    module whose function made the read. An untracked file, and a listing
    outside testing/, count for none."""
    for rel, text in STAND_IN.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True)
    (tmp_path / "testing/data/untracked.csv").write_text("")
    (tmp_path / "testing/alone.sh").chmod(0o755)
    out = tmp_path / "out"
    env = {**os.environ, "ORACLE_DIR": str(out), "PYTHONPATH": str(TOOLS)}
    env = {k: v for k, v in env.items() if not k.startswith("GIT_")}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "opens_oracle",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
        ],
        cwd=tmp_path / "server",
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    rows = oracle.read_rows(out)
    server = tmp_path / "server"
    tests = ["test_reads.py", "test_imports.py", "test_top.py", "test_util.py"]
    found = {
        pair: sorted(kinds) for pair, kinds in oracle.pairs(rows, server, tests).items()
    }
    assert found == {
        ("testing/data/roster.csv", "test_reads.py"): ["open"],
        ("testing/data/faces", "test_reads.py"): ["list"],
        ("testing/run.sh", "test_reads.py"): ["popen"],
        ("testing/shell.sh", "test_reads.py"): ["popen"],
        ("testing/alone.sh", "test_reads.py"): ["popen"],
        ("testing/data/upload2.csv", "test_reads.py"): ["upload"],
        ("testing/data/thread.csv", "test_reads.py"): ["open"],
        ("testing/data/relative.csv", "test_reads.py"): ["open"],
        ("testing/data/upload.csv", "test_reads.py"): ["upload"],
        ("testing/run.sh", "test_imports.py"): ["import-time"],
        ("testing/run.sh", "test_top.py"): ["import-time"],
    }


BROWSER = "import pytest\n\npytestmark = pytest.mark.browser\n"


def test_the_report_marks_each_pair_affected_tests_maps(tmp_path):
    """The report, run in a stand-in with the repository's affected-tests,
    names each pair mapped or UNMAPPED, a listing as a listing, and counts
    the unmapped file pairs; a top-level read reaches a browser test that
    imports its module, and no plain test that does."""
    out, root = tmp_path / "out", tmp_path / "repo"
    repo = Path(__file__).resolve().parents[2]
    files = {
        "plan/bin/affected-tests": (repo / "plan/bin/affected-tests").read_text(),
        "server/tools/opens_oracle.py": (TOOLS / "opens_oracle.py").read_text(),
        ".gitignore": "__pycache__/\n",
        "testing/data/roster.csv": "",
        "testing/data/other.csv": "",
        "server/helpers/rows.py": "",
        "server/ui/tests/browser/test_cites.py": BROWSER + 'CSV = "roster.csv"\n',
        "server/ui/tests/browser/test_quiet.py": BROWSER + "import helpers.rows\n",
        "server/ui/tests/test_plain.py": "import helpers.rows\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for argv in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t"],
    ):
        subprocess.run(["git", *argv], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    out.mkdir()
    cites, quiet = "ui/tests/browser/test_cites.py", "ui/tests/browser/test_quiet.py"
    rows = [
        {
            "kind": "open",
            "path": "testing/data/roster.csv",
            "test": f"{cites}::t",
            "modules": [],
        },
        {
            "kind": "open",
            "path": "testing/data/other.csv",
            "test": f"{cites}::t",
            "modules": [],
        },
        {"kind": "list", "path": "testing/data", "test": f"{quiet}::t", "modules": []},
        {
            "kind": "open",
            "path": "testing/data/roster.csv",
            "test": None,
            "modules": ["helpers/rows.py"],
        },
    ]
    (out / "1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def report():
        argv = [
            sys.executable,
            "-I",
            str(root / "server/tools/opens_oracle.py"),
            str(out),
        ]
        return subprocess.run(
            argv, env=env, capture_output=True, text=True, check=True
        ).stdout.splitlines()

    assert report() == [
        f"worktree {root} head {head}; uncommitted: no",
        "pairs 4; testing/ paths 3",
        f"listing\tlist\ttesting/data\t{quiet}",
        f"UNMAPPED\topen\ttesting/data/other.csv\t{cites}",
        f"mapped\topen\ttesting/data/roster.csv\t{cites}",
        f"UNMAPPED\timport-time\ttesting/data/roster.csv\t{quiet}",
        "unmapped file pairs 2",
    ]
    (root / "stray").write_text("")
    assert report()[0] == f"worktree {root} head {head}; uncommitted: yes"
