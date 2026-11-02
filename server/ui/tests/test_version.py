import subprocess
from unittest import mock

import pytest

from ui import version


@pytest.fixture(autouse=True)
def fresh():
    version.build_version.cache_clear()
    yield
    version.build_version.cache_clear()


def git_answers(branch, commit):
    return mock.patch.object(
        version,
        "_git",
        side_effect=lambda *args: branch if "--abbrev-ref" in args else commit,
    )


def test_the_environment_wins(monkeypatch):
    monkeypatch.setenv("DERBYNET_VERSION", "2026.11")

    with git_answers("issue-40", "abc1234"):
        assert version.build_version() == "2026.11"


def test_master_reads_as_dev(monkeypatch):
    monkeypatch.delenv("DERBYNET_VERSION", raising=False)

    with git_answers("master", "abc1234"):
        assert version.build_version() == "dev abc1234"


def test_a_branch_shows_with_its_commit(monkeypatch):
    monkeypatch.delenv("DERBYNET_VERSION", raising=False)

    with git_answers("issue-40-ui-shell", "abc1234"):
        assert version.build_version() == "issue-40-ui-shell abc1234"


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("git"), subprocess.CalledProcessError(128, "git")],
    ids=["no-git", "no-checkout"],
)
def test_dev_when_git_cannot_say(monkeypatch, failure):
    monkeypatch.delenv("DERBYNET_VERSION", raising=False)

    with mock.patch.object(version, "_git", side_effect=failure):
        assert version.build_version() == "dev"
