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
