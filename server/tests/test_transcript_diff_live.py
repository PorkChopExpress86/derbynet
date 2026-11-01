"""The transcript differ (#37) on two real runs of the shell suite.

A Django dev server runs as `.github/workflows/server.yml` starts it, on a
fresh SQLite database of its own. `testing/reset-database.sh` and
`testing/import-roster.sh` run twice from separate copies of `testing/`, the
second time with ids shifted by `testing/shift-ids.sh`, which runs from a
third copy so its exchanges stay out of the transcript. Each recorded run
starts from a database just reset, so both see the same rows.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tools import transcript_diff as td

SERVER_DIR = Path(__file__).resolve().parent.parent
TESTING = SERVER_DIR.parent / "testing"

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("bash", "curl", "jq", "xmllint")),
    reason="the shell suite needs bash, curl, jq and xmllint",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("server")
    env = {
        **os.environ,
        "DATABASE_URL": "",
        "DERBYNET_DATA_DIR": str(root / "data"),
        "DERBYNET_CONFIG_DIR": str(root / "config"),
        "DERBYNET_ENABLE_TEST_ENDPOINTS": "1",
    }
    manage = [sys.executable, "manage.py"]
    for command in (["migrate", "-v0"], ["create_test_event"]):
        subprocess.run(manage + command, cwd=SERVER_DIR, env=env, check=True)
    url = f"http://127.0.0.1:{free_port()}"
    with open(root / "server.log", "wb") as log:
        process = subprocess.Popen(
            manage + ["runserver", url.removeprefix("http://"), "--noreload"],
            cwd=SERVER_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{url}/action.php?query=role.list", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail((root / "server.log").read_text())
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def copy_of_testing(where: Path) -> Path:
    """A directory holding a copy of the suite's scripts in `testing/`, where
    `common.sh` keeps its own `output.curl` and `cookies.curl`."""
    (where / "testing").mkdir(parents=True)
    for path in TESTING.iterdir():
        if path.is_file() and not path.name.endswith(".curl"):
            shutil.copy2(path, where / "testing" / path.name)
    return where


def bash(where: Path, script: str, url: str) -> None:
    result = subprocess.run(
        ["bash", f"testing/{script}", url], cwd=where, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def record(url: str, where: Path, shift: bool) -> str:
    recorded, side = copy_of_testing(where / "run"), copy_of_testing(where / "side")
    bash(side, "reset-database.sh", url)
    if shift:
        bash(side, "shift-ids.sh", url)
        bash(side, "reset-database.sh", url)
    shutil.copy(side / "testing/cookies.curl", recorded / "testing/cookies.curl")
    bash(recorded, "reset-database.sh", url)
    bash(recorded, "import-roster.sh", url)
    return (recorded / "testing/output.curl").read_text()


@pytest.fixture(scope="module")
def runs(server, tmp_path_factory):
    plain = record(server, tmp_path_factory.mktemp("plain"), shift=False)
    shifted = record(server, tmp_path_factory.mktemp("shifted"), shift=True)
    return plain, shifted


def racerids(text: str) -> list[int]:
    return [
        int(line.split(":")[1].strip(" ,"))
        for line in text.splitlines()
        if line.strip().startswith('"racerid":')
    ]


def test_two_runs_with_shifted_ids_match(runs):
    plain, shifted = runs

    report = td.diff(plain, shifted)

    # The ids did change, and only the normalization hides them.
    assert racerids(plain)[0] != racerids(shifted)[0]
    assert plain != shifted
    assert report.ok, report.render()
    assert report.compared == len(td.parse(plain)) > 90


def test_a_changed_value_in_a_real_run_fails(runs):
    plain, shifted = runs
    corrupt = shifted.replace('"firstname": "Ian"', '"firstname": "Iain"', 1)

    report = td.diff(plain, corrupt)

    [found] = report.differences
    assert found.endpoint == "action racer.import"
    assert "firstname=Ian&lastname=Ives" in found.request
    assert found.path == "$.action.firstname"
    assert (found.a, found.b) == ("Ian", "Iain")
