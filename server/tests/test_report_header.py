"""Every pytest log names the tree it ran on: its folder and its commit.

#324's first hand-off logs held pytest's output alone, and the head lived in
a summary file another agent's run shared, so nothing tied them to the
worktree and commit, and the lead asked for the set again (#324 retro).
pytest's own header prints `rootdir`, which names the worktree but not the
commit."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace


def test_the_header_names_the_folder_the_commit_and_uncommitted_changes(request):
    config = request.config
    lines = [
        line
        for found in config.hook.pytest_report_header(
            config=config, start_path=Path.cwd()
        )
        for line in ([found] if isinstance(found, str) else found)
    ]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert f"cwd: {os.getcwd()}" in lines
    assert f"head: {head}" + (" with uncommitted changes" if dirty else "") in lines


def _hook(config):
    """The root conftest's `pytest_report_header`, as pytest registered it."""
    conftest = str(Path(__file__).resolve().parents[1] / "conftest.py")
    for plugin in config.pluginmanager.get_plugins():
        if getattr(plugin, "__file__", None) == conftest:
            return plugin.pytest_report_header
    raise AssertionError("the root conftest is registered")


def test_the_commit_is_the_rootdir_s_from_any_folder(request, tmp_path, monkeypatch):
    """pytest started from another folder, with paths, still names the
    commit of the tree it runs (#340 review)."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)
    lines = _hook(request.config)(request.config)
    assert any(line.startswith(f"head: {head}") for line in lines), lines


def test_a_tree_outside_git_is_named_so(request, tmp_path):
    lines = _hook(request.config)(SimpleNamespace(rootpath=tmp_path))
    assert "head: unknown (not a git checkout)" in lines


def test_no_git_on_the_path_is_named_so(request, monkeypatch):
    monkeypatch.setenv("PATH", "")
    lines = _hook(request.config)(request.config)
    assert "head: unknown (no git)" in lines


def _held(path, holder="pid 42, since now\ncwd /there\n"):
    """`path` locked as tools/pytest-under-load.sh locks it, with its lines."""
    import fcntl

    path.write_text(holder)
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_a_load_run_holding_its_lock_is_named(request, tmp_path, monkeypatch):
    """#316's hand-off ran beside #317's load run at a load average of 35
    and failed a test that passes alone (#316 retro)."""
    lock = tmp_path / "load.lock"
    monkeypatch.setenv("DERBYNET_LOAD_LOCK", str(lock))
    monkeypatch.delenv("DERBYNET_LOAD_LOCK_HELD", raising=False)
    named = (
        f"load run: tools/pytest-under-load.sh holds {lock} "
        "(pid 42, since now; cwd /there); results beside it are not clean"
    )
    assert named not in _hook(request.config)(request.config)  # no file yet
    fd = _held(lock)
    try:
        assert named in _hook(request.config)(request.config)
        written = []
        conftest = _hook(request.config).__globals__
        conftest["pytest_terminal_summary"](
            SimpleNamespace(write_line=lambda line, **_: written.append(line)),
            SimpleNamespace(),
        )
        assert written == [named]
    finally:
        os.close(fd)
    assert named not in _hook(request.config)(request.config)  # released
    assert lock.read_text() == "pid 42, since now\ncwd /there\n"  # only read


def test_the_load_run_s_own_pytest_names_nothing(request, tmp_path, monkeypatch):
    lock = tmp_path / "load.lock"
    monkeypatch.setenv("DERBYNET_LOAD_LOCK", str(lock))
    monkeypatch.setenv("DERBYNET_LOAD_LOCK_HELD", str(lock))
    fd = _held(lock)
    try:
        lines = _hook(request.config)(request.config)
        assert not [line for line in lines if line.startswith("load run:")]
    finally:
        os.close(fd)


def test_the_default_lock_is_the_script_s(request, tmp_path, monkeypatch):
    """Without DERBYNET_LOAD_LOCK both read ~/.cache/derbynet/load-run.lock,
    as the script's `Load lock:` line names it."""
    monkeypatch.delenv("DERBYNET_LOAD_LOCK", raising=False)
    monkeypatch.delenv("DERBYNET_LOAD_LOCK_HELD", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "tools" / "pytest-under-load.sh"
    out = tmp_path / "out"
    with out.open("w") as stdout:
        subprocess.run(
            [str(script), "--loops", "0", "--collect-only", "-q", str(tmp_path)],
            stdout=stdout,
            stderr=subprocess.STDOUT,
            env={**os.environ, "DERBYNET_LOAD_RUN_DIR": str(tmp_path / "run")},
            timeout=120,
        )
    lock = tmp_path / ".cache" / "derbynet" / "load-run.lock"
    assert f"Load lock: {lock}\n" in out.read_text()
    fd = _held(lock)
    try:
        lines = _hook(request.config)(request.config)
        assert any(
            line.startswith(f"load run: tools/pytest-under-load.sh holds {lock} ")
            for line in lines
        )
    finally:
        os.close(fd)
