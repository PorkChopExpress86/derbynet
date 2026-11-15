""".github/ubuntu-mirror.sh puts its mirror first only when the mirror serves
a package index at a usable speed.  On 2026-11-13 a download from
mirrors.edge.kernel.org hung for 39 min until CI cancelled the job (run
43366304478); a mirror like that now leaves the runner's list unchanged.

On 2026-11-14 the same download hung for 49 min after a probe that passed
(run 43640908879).  apt's own timeout counts only silence, and a server
that sends a byte every few seconds holds apt-get for as long as it likes
(the trickling-mirror test below).  So the script bounds each
apt-get with a shim, which takes the mirror out and runs apt-get again,
and writes an apt.conf drop-in so a silent mirror costs seconds."""

import hashlib
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
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
    """/repo serves a one-suite apt repository, and /drip and /silent hold
    every download from it (a byte every half second, or nothing after the
    first 100),
    /fast serves the index at once, /stall sends its headers and a few
    bytes and then nothing, /trickle sends 200 kB/s, above the probe's
    50 kB/s floor, for 50 s, /moved redirects, /empty answers 200 with no
    body, /missing has no index."""

    requested: list[str] = []

    def do_GET(self):
        Mirror.requested.append(self.path)
        if self.path.startswith("/repo/ubuntu/dists/"):
            body = REPO.get(self.path.removeprefix("/repo/ubuntu"))
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/drip/ubuntu/dists/"):
            # A byte every half second: never silent for apt's timeout.
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            for _ in range(120):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.5)
        elif self.path.startswith("/silent/ubuntu/dists/"):
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            self.wfile.write(b"x" * 100)
            self.wfile.flush()
            time.sleep(60)
        elif self.path == "/fast/ubuntu" + INDEX:
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
        elif self.path == "/trickle/ubuntu" + INDEX:
            self.send_response(200)
            self.send_header("Content-Length", "10000000")
            self.end_headers()
            for _ in range(500):
                self.wfile.write(b"x" * 20_000)
                self.wfile.flush()
                time.sleep(0.1)
        elif self.path == "/moved/ubuntu" + INDEX:
            self.send_response(302)
            self.send_header("Location", "/fast/ubuntu" + INDEX)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/empty/ubuntu" + INDEX:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
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


# A one-package index, and a Release file that lists it: enough for
# `apt-get update` from a `[trusted=yes]` source.
PACKAGES = b"Package: derbynet-probe\nVersion: 1\nArchitecture: all\n\n"
PACKAGES_PATH = "/dists/noble/main/binary-amd64/Packages"
REPO = {PACKAGES_PATH: PACKAGES}
REPO["/dists/noble/Release"] = (
    "Suite: noble\nCodename: noble\nDate: Thu, 01 Jan 2026 00:00:00 UTC\n"
    "Components: main\nArchitectures: amd64\n"
    "SHA256:\n"
    f" {hashlib.sha256(PACKAGES).hexdigest()} {len(PACKAGES)}"
    " main/binary-amd64/Packages\n"
).encode()


def run(tmp_path, mirror, listed=RUNNER, **env):
    mirrors = tmp_path / "apt-mirrors.txt"
    if listed is not None:
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
            "APT_CONF": str(tmp_path / "80derbynet-timeouts"),
            "APT_GET_SHIM": str(tmp_path / "apt-get"),
            "APT_GET": str(tmp_path / "real-apt-get"),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    listed = mirrors.read_text() if mirrors.exists() else None
    return listed, done.stdout, time.monotonic() - started


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


def test_a_mirror_slower_than_its_whole_bound_leaves_the_list(tmp_path, server):
    """The whole bound is 5 s, and the handler sends for 50 s at a speed
    the stall bound passes."""
    listed, printed, took = run(tmp_path, f"{server}/trickle/ubuntu/")

    assert listed == RUNNER
    assert "failed the probe" in printed
    assert took < 8


@pytest.mark.parametrize("answer", ["missing", "moved", "empty"])
def test_a_mirror_with_no_index_leaves_the_list_and_prints_it(tmp_path, server, answer):
    listed, printed, _ = run(tmp_path, f"{server}/{answer}/ubuntu/")

    assert listed == RUNNER
    assert printed.startswith(f"ubuntu-mirror.sh: {server}/{answer}/ubuntu/")
    assert "failed the probe" in printed
    assert printed.endswith(RUNNER)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_list_that_has_the_mirror_is_left_unprobed(tmp_path, server, scheme):
    """By its host, whatever the scheme or path the list gives it."""
    mirror = f"{server}/fast/ubuntu/"
    host = server.split("://")[1]
    already = f"{scheme}://{host}/ubuntu\tpriority:1\n" + RUNNER

    listed, _, _ = run(tmp_path, mirror, already)

    assert listed == already
    assert Mirror.requested == []


def test_the_script_writes_apt_timeouts_whether_or_not_the_mirror_goes_first(
    tmp_path, server
):
    for answer in ("fast", "missing"):
        run(tmp_path, f"{server}/{answer}/ubuntu/", APT_TIMEOUT_SECONDS="7")

        assert (tmp_path / "80derbynet-timeouts").read_text().splitlines()[1:] == [
            'Acquire::http::Timeout "7";',
            'Acquire::https::Timeout "7";',
            'Acquire::Retries "1";',
        ]


def fake_apt_get(tmp_path, script):
    """The shim's real apt-get: it notes its arguments in `calls`, then runs
    `script`."""
    fake = tmp_path / "real-apt-get"
    fake.write_text(f'#!/bin/sh\necho "$*" >> {tmp_path}/calls\n{script}\n')
    fake.chmod(0o755)
    return tmp_path / "calls"


def shim(tmp_path, *args, **env):
    started = time.monotonic()
    done = subprocess.run(
        [str(tmp_path / "apt-get"), *args],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, time.monotonic() - started


def test_the_shim_runs_apt_get_once_with_its_arguments(tmp_path, server):
    mirror = f"{server}/fast/ubuntu/"
    listed, _, _ = run(tmp_path, mirror)
    calls = fake_apt_get(tmp_path, "exit 0")

    done, _ = shim(tmp_path, "install", "-y", "imagemagick")

    assert done.returncode == 0
    assert calls.read_text() == "install -y imagemagick\n"
    assert (tmp_path / "apt-mirrors.txt").read_text() == listed
    assert "at most 300 s" in done.stderr


def test_an_apt_get_out_of_time_loses_the_mirror_and_runs_again(tmp_path, server):
    """The first run hangs while the mirror is listed, past the shim's 2 s;
    the second, without it, passes.  A second run of the script in the same
    job leaves the mirror out."""
    mirror = f"{server}/fast/ubuntu/"
    run(tmp_path, mirror, APT_GET_SECONDS="2")
    calls = fake_apt_get(
        tmp_path, f"grep -q /fast/ {tmp_path}/apt-mirrors.txt && sleep 30\nexit 0"
    )

    done, took = shim(tmp_path, "update")

    assert done.returncode == 0, done.stderr
    assert took < 10
    assert calls.read_text() == "update\nupdate\n"
    assert f"failed (exit 124, 124 is out of time); {mirror} leaves" in done.stderr
    without = (
        "http://azure.archive.ubuntu.com/ubuntu/\tpriority:2\n"
        "https://archive.ubuntu.com/ubuntu/\tpriority:3\n"
        "https://security.ubuntu.com/ubuntu/\tpriority:4\n"
    )
    assert (tmp_path / "apt-mirrors.txt").read_text() == without

    listed, printed, _ = run(tmp_path, mirror, listed=None)

    assert listed == without
    assert Mirror.requested == []
    assert "failed earlier in this job" in printed


def test_a_failing_apt_get_with_no_mirror_listed_keeps_its_status(tmp_path, server):
    run(tmp_path, f"{server}/missing/ubuntu/")
    calls = fake_apt_get(tmp_path, "exit 100")

    done, _ = shim(tmp_path, "install", "-y", "nothing")

    assert done.returncode == 100
    assert calls.read_text() == "install -y nothing\n"
    assert (tmp_path / "apt-mirrors.txt").read_text() == RUNNER


HOLD_LOCK = """
import fcntl, os, sys, time
lock = open(sys.argv[1], "w")
fcntl.lockf(lock, fcntl.LOCK_EX)
open(sys.argv[2], "w").write(str(os.getpid()))
time.sleep(30)
"""


def test_no_second_apt_get_runs_while_the_dpkg_lock_is_held(tmp_path, server):
    """#318's review: apt-get ran out of time while dpkg configured a package.
    dpkg runs in a session of its own, so the kill leaves it holding its lock,
    and a second apt-get fails on that lock.  Here the real
    apt-get starts a holder of a POSIX lock in a new session, as apt starts
    dpkg, then hangs past the shim's 2 s.  The holder's output goes to
    /dev/null, or the test would wait on its pipes."""
    mirror = f"{server}/fast/ubuntu/"
    lock = tmp_path / "lock"
    lock.touch()
    held = tmp_path / "holder"
    listed, _, _ = run(tmp_path, mirror, APT_GET_SECONDS="2", DPKG_LOCK=str(lock))
    calls = fake_apt_get(
        tmp_path,
        f"setsid {sys.executable} -c '{HOLD_LOCK}' {lock} {held}"
        " </dev/null >/dev/null 2>&1 &\n"
        # dash, /bin/sh here, has no $SECONDS; 300 steps of 0.1 s bound the wait.
        f'i=0; until [ -s {held} ] || [ "$i" -ge 300 ]; do i=$((i + 1)); sleep 0.1; done\n'
        "sleep 30",
    )
    try:
        done, took = shim(tmp_path, "install", "-y", "slowpkg")
        pid = held.read_text()
        name = Path(f"/proc/{pid}/comm").read_text().strip()
    finally:
        os.kill(int(held.read_text()), signal.SIGKILL)

    assert done.returncode == 124
    assert took < 10
    assert calls.read_text() == "install -y slowpkg\n"
    assert (
        f"and process {pid} ({name}) still holds {lock}; a second apt-get would"
        " fail on that lock, so none runs"
    ) in done.stderr
    assert (tmp_path / "apt-mirrors.txt").read_text() == listed


def test_a_lock_nobody_holds_lets_apt_get_run_again(tmp_path, server):
    """The same hang with the lock file there and free: the mirror leaves
    and apt-get runs a second time."""
    mirror = f"{server}/fast/ubuntu/"
    lock = tmp_path / "lock"
    lock.touch()
    run(tmp_path, mirror, APT_GET_SECONDS="2", DPKG_LOCK=str(lock))
    calls = fake_apt_get(
        tmp_path, f"grep -q /fast/ {tmp_path}/apt-mirrors.txt && sleep 30\nexit 0"
    )

    done, _ = shim(tmp_path, "update")

    assert done.returncode == 0, done.stderr
    assert calls.read_text() == "update\nupdate\n"
    assert "still holds" not in done.stderr


def test_a_runner_with_no_mirror_list_still_gets_the_bounds(tmp_path, server):
    listed, printed, _ = run(tmp_path, f"{server}/fast/ubuntu/", listed=None)
    calls = fake_apt_get(tmp_path, "exit 100")

    done, _ = shim(tmp_path, "update")

    assert listed is None
    assert printed == ""
    assert Mirror.requested == []
    assert (tmp_path / "80derbynet-timeouts").exists()
    assert done.returncode == 100
    assert calls.read_text() == "update\n"


APT_GET = shutil.which("apt-get", path="/usr/bin:/bin")


def real_update(tmp_path, server, held, **env):
    """`apt-get update`, through the shim, from a mirror list with `held`
    first and /repo second: the real apt-get, as this user, with every
    directory in tmp_path.  APT_CONFIG is read before apt.conf.d, so the
    script's drop-in there is the one apt reads.  /repo has no Release.gpg,
    and apt may keep no index for that, so a test reads what apt asked the
    two mirrors for."""
    etc = tmp_path / "etc"
    (etc / "apt.conf.d").mkdir(parents=True)
    for folder in ("lists/partial", "cache/archives/partial"):
        (tmp_path / folder).mkdir(parents=True)
    held = f"{server}/{held}/ubuntu/"
    mirrors = tmp_path / "apt-mirrors.txt"
    (etc / "sources.list").write_text(
        f"deb [trusted=yes] mirror+file:{mirrors} noble main\n"
    )
    (etc / "apt.conf").write_text(
        f'Dir::Etc "{etc}/";\n'
        f'Dir::State::Lists "{tmp_path}/lists/";\n'
        f'Dir::Cache "{tmp_path}/cache/";\n'
        'Debug::NoLocking "true";\n'
        f'APT::Sandbox::User "{pwd.getpwuid(os.getuid()).pw_name}";\n'
        'Acquire::Languages "none";\n'
    )
    # Listed already, as when the probe passed and the stall came later.
    run(
        tmp_path,
        held,
        listed=f"{held}\tpriority:1\n{server}/repo/ubuntu/\tpriority:2\n",
        APT_CONF=str(etc / "apt.conf.d" / "80derbynet-timeouts"),
        APT_GET=APT_GET,
        APT_TIMEOUT_SECONDS="1",
        APT_RETRIES="0",
        **env,
    )
    done, took = shim(tmp_path, "update", APT_CONFIG=str(etc / "apt.conf"))
    return done, took, mirrors.read_text()


needs_apt = pytest.mark.skipif(APT_GET is None, reason="no apt-get on this host")


@needs_apt
def test_apt_falls_back_from_a_silent_mirror_within_the_drop_in_timeout(
    tmp_path, server
):
    """apt gives the silent mirror up after the drop-in's 1 s, file by file,
    and the update ends well inside the shim's bound."""
    done, took, listed = real_update(tmp_path, server, "silent", APT_GET_SECONDS="60")

    assert done.returncode == 0, done.stderr
    assert took < 20
    assert f"{server}/silent/ubuntu/\tpriority:1\n" in listed
    assert Mirror.requested[0] == "/silent/ubuntu/dists/noble/InRelease"
    assert Mirror.requested[-1] == "/repo/ubuntu" + PACKAGES_PATH


@needs_apt
def test_apt_held_by_a_trickling_mirror_is_ended_and_runs_without_it(tmp_path, server):
    """A byte every half second never trips apt's 1 s timeout; the shim's
    4 s bound ends it, and apt-get runs again from /repo."""
    done, took, listed = real_update(tmp_path, server, "drip", APT_GET_SECONDS="4")

    assert done.returncode == 0, done.stderr
    assert re.search(r"failed \(exit 124, .*/drip/ubuntu/ leaves", done.stderr)
    assert took < 20
    assert listed == f"{server}/repo/ubuntu/\tpriority:2\n"
    assert Mirror.requested[0] == "/drip/ubuntu/dists/noble/InRelease"
    assert Mirror.requested[-1] == "/repo/ubuntu" + PACKAGES_PATH


WORKFLOWS = SCRIPT.parent / "workflows"
# A line that starts a step, whichever key the step lists first.
STEP = re.compile(r"^\s*- (?:name|uses|run|id|if|env|with|shell|working-directory):")
RUNS_APT = re.compile(r"\bapt-get\b|\bapt install\b|--with-deps\b")


def apt_without_the_script(text: str) -> tuple[int, list[int]]:
    """The number of steps that run apt, and the line number of each apt line
    with no `ubuntu-mirror.sh` above it in its step.  Comment lines are
    skipped."""
    steps, bare = 0, []
    mirrored = counted = False
    for number, line in enumerate(text.splitlines(), 1):
        if STEP.match(line):
            mirrored = counted = False
        if line.lstrip().startswith("#"):
            continue
        if "ubuntu-mirror.sh" in line:
            mirrored = True
        elif RUNS_APT.search(line):
            steps += not counted
            counted = True
            if not mirrored:
                bare.append(number)
    return steps, bare


def test_every_workflow_step_runs_the_script_before_apt():
    """The shim it writes bounds each apt-get, playwright's --with-deps
    included; a step that runs apt before it waits as long as apt likes."""
    found = {
        path.name: apt_without_the_script(path.read_text())
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }
    assert {name: bare for name, (_, bare) in found.items() if bare} == {}
    assert sum(steps for steps, _ in found.values()) >= 10


def test_the_step_scan_finds_apt_before_the_script():
    steps = """\
      - name: Install
        run: |
          sudo apt-get update
          sudo sh .github/ubuntu-mirror.sh
      - name: Chromium
        run: |
          # apt-get runs inside playwright
          sudo sh "$GITHUB_WORKSPACE/.github/ubuntu-mirror.sh"
          uv run playwright install --with-deps chromium
      - run: uv run playwright install --with-deps chromium
"""
    assert apt_without_the_script(steps) == (3, [3, 10])


@pytest.mark.parametrize("first", [True, False], ids=["shim first", "shim missed"])
def test_the_script_warns_when_the_path_misses_the_shim(tmp_path, server, first):
    """sudo's secure_path and the runner's PATH put /usr/local/sbin ahead of
    /usr/bin; the warning says so in the job's log if one does not."""
    (tmp_path / "apt-mirrors.txt").write_text(RUNNER)
    done = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin" if first else "/usr/bin:/bin",
            "APT_MIRRORS_LIST": str(tmp_path / "apt-mirrors.txt"),
            "UBUNTU_MIRROR": f"{server}/missing/ubuntu/",
            "UBUNTU_CODENAME": "noble",
            "APT_CONF": str(tmp_path / "80derbynet-timeouts"),
            "APT_GET_SHIM": str(tmp_path / "apt-get"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert done.returncode == 0
    warned = f"not {tmp_path}/apt-get; apt-get runs unbounded" in done.stderr
    assert warned is not first
