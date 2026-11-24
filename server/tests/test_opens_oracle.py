"""`tools/opens_oracle.py`, #470's runtime oracle for affected-tests' testing/
rules, run as a pytest plugin over a stand-in repository: each kind of read
it records reaches the report as a pair with the test that made it."""

import importlib.util
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
    "testing/data/faces/one.jpg": "",
    "testing/run.sh": "#!/bin/bash\n",
    "testing/never.txt": "",
    "server/pytest.ini": "[pytest]\n",
    "server/helpers/__init__.py": "",
    "server/helpers/rows.py": (
        "from pathlib import Path\n"
        "TESTING = Path(__file__).resolve().parents[2] / 'testing'\n"
        "SCRIPT = (TESTING / 'run.sh').read_text()\n"
    ),
    "server/test_reads.py": (
        "import asyncio\n"
        "import os\n"
        "import subprocess\n"
        "import threading\n"
        "from pathlib import Path\n\n"
        "TESTING = Path(__file__).resolve().parents[1] / 'testing'\n\n\n"
        "def test_opens():\n"
        "    (TESTING / 'data' / 'roster.csv').read_text()\n\n\n"
        "def test_lists():\n"
        "    sorted((TESTING / 'data' / 'faces').iterdir())\n\n\n"
        "def test_runs():\n"
        "    subprocess.run(['true', str(TESTING / 'run.sh')], check=True)\n\n\n"
        "def test_reads_in_a_thread():\n"
        "    path = TESTING / 'data' / 'thread.csv'\n"
        "    read = threading.Thread(target=path.read_text)\n"
        "    read.start()\n"
        "    read.join()\n\n\n"
        "def test_uploads():\n"
        "    from playwright._impl import _frame\n"
        "    try:\n"
        "        files = [TESTING / 'data' / 'upload.csv']\n"
        "        asyncio.run(_frame.convert_input_files(files, None))\n"
        "    except Exception:\n"
        "        pass  # the driver's half needs a browser context\n\n\n"
        "def test_nothing():\n"
        "    os.getcwd()\n"
    ),
    "server/test_imports.py": "def test_a():\n    from helpers import rows\n",
    "server/test_top.py": "from helpers import rows\n\n\ndef test_b():\n    pass\n",
}


def test_each_read_maps_to_the_test_that_made_it(tmp_path):
    """An open, in the test's thread or another, a listing, a command line
    and a file handed to Playwright's set_input_files while a test runs
    count for that test; a read at a module's top level during collection counts for
    each test file that imports the module, by the oracle's own graph."""
    for rel, text in STAND_IN.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True)
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
    tests = ["test_reads.py", "test_imports.py", "test_top.py"]
    found = {
        pair: sorted(kinds) for pair, kinds in oracle.pairs(rows, server, tests).items()
    }
    assert found == {
        ("testing/data/roster.csv", "test_reads.py"): ["open"],
        ("testing/data/faces", "test_reads.py"): ["list"],
        ("testing/run.sh", "test_reads.py"): ["popen"],
        ("testing/data/thread.csv", "test_reads.py"): ["open"],
        ("testing/data/upload.csv", "test_reads.py"): ["upload"],
        ("testing/run.sh", "test_imports.py"): ["import-time"],
        ("testing/run.sh", "test_top.py"): ["import-time"],
    }
