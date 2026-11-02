"""The build version the drawer and the sidebar show, as PHP's banner shows
`@BRANCH@` (#40)."""

import os
import subprocess
from functools import cache

from django.conf import settings


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=settings.BASE_DIR,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()


@cache
def build_version() -> str:
    """`DERBYNET_VERSION`, which an image build sets. Otherwise the checkout's
    branch, with `master` read as `dev` as the ant build does, and its commit.
    `dev` when neither is known."""
    if version := os.environ.get("DERBYNET_VERSION", "").strip():
        return version
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        commit = _git("rev-parse", "--short", "HEAD")
    except OSError, subprocess.SubprocessError:
        return "dev"
    branch = "dev" if branch == "master" else branch
    return f"{branch} {commit}"
