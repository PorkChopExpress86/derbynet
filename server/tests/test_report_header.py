"""Every pytest log names the tree it ran on: its folder and its commit.

#324's first hand-off logs held pytest's output alone, and the head lived in
a summary file another agent's run shared, so nothing tied them to the
worktree and commit, and the lead asked for the set again (#324 retro). pytest's own header prints `rootdir`, which names the worktree but
not the commit."""

import os
import subprocess
from pathlib import Path


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
