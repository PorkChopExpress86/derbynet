""".github/ubuntu-mirror.sh puts its mirror first only when the mirror serves
a package index at a usable speed.  On 2026-11-13 a download from
mirrors.edge.kernel.org hung for 39 min until CI cancelled the job (run
43366304478); a mirror like that now leaves the runner's list unchanged."""

import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "ubuntu-mirror.sh"
INDEX = "/dists/noble-updates/main/binary-amd64/Packages.gz"
RUNNER = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)


class Mirror(BaseHTTPRequestHandler):
    """/fast serves the index at once, /stall sends its headers and a few
    bytes and then nothing, /missing has no index."""

    requested: list[str] = []

    def do_GET(self):
        Mirror.requested.append(self.path)
        if self.path == "/fast/ubuntu" + INDEX:
            body = b"x" * 1_000_000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stall/ubuntu" + INDEX:
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            self.wfile.write(b"x" * 100)
            self.wfile.flush()
            time.sleep(8)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Mirror)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def run(tmp_path, mirror, listed=RUNNER):
    mirrors = tmp_path / "apt-mirrors.txt"
    mirrors.write_text(listed)
    Mirror.requested.clear()
    started = time.monotonic()
    done = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "PATH": "/usr/bin:/bin",
            "APT_MIRRORS_LIST": str(mirrors),
            "UBUNTU_MIRROR": mirror,
            "UBUNTU_CODENAME": "noble",
            "MIRROR_STALL_SECONDS": "1",
            "MIRROR_PROBE_SECONDS": "5",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return mirrors.read_text(), done.stdout, time.monotonic() - started


def test_a_mirror_that_serves_the_index_goes_first(tmp_path, server):
    mirror = f"{server}/fast/ubuntu/"

    listed, printed, _ = run(tmp_path, mirror)

    assert listed == (
        f"{mirror}\tpriority:1\n"
        "http://azure.archive.ubuntu.com/ubuntu/\tpriority:2\n"
        "https://archive.ubuntu.com/ubuntu/\tpriority:3\n"
        "https://security.ubuntu.com/ubuntu/\tpriority:4\n"
    )
    assert printed == listed
    assert Mirror.requested == ["/fast/ubuntu" + INDEX]


def test_a_mirror_that_stalls_leaves_the_list_and_ends_within_its_bound(
    tmp_path, server
):
    """The stall bound is 1 s, and the handler holds its connection 8 s."""
    listed, printed, took = run(tmp_path, f"{server}/stall/ubuntu/")

    assert listed == RUNNER
    assert "failed the probe" in printed
    assert took < 5


def test_a_mirror_with_no_index_leaves_the_list(tmp_path, server):
    listed, printed, _ = run(tmp_path, f"{server}/missing/ubuntu/")

    assert listed == RUNNER
    assert "failed the probe" in printed


def test_a_list_that_has_the_mirror_is_left_unprobed(tmp_path, server):
    mirror = f"{server}/fast/ubuntu/"
    already = f"{mirror}\tpriority:1\n" + RUNNER

    listed, _, _ = run(tmp_path, mirror, already)

    assert listed == already
    assert Mirror.requested == []
