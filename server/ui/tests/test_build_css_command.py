"""`manage.py build_css` with a stand-in Tailwind CLI (#40)."""

import hashlib
import io

import pytest
from django.core.management import CommandError, call_command

from ui import css_build

# A stand-in CLI: writes a fixed stylesheet to the path after --output.
FAKE_CLI = b"""#!/bin/sh
while [ "$1" != "--output" ]; do shift; done
printf '.built{}' > "$2"
"""


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The command reads the cache under a temporary home, and the release
    "download" is the stand-in, with its own checksum pinned."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("DERBYNET_TAILWIND", raising=False)
    platform = css_build._platform()
    monkeypatch.setitem(
        css_build.SHA256, platform, hashlib.sha256(FAKE_CLI).hexdigest()
    )
    downloads = []

    def urlopen(url, timeout):
        downloads.append(url)
        return io.BytesIO(FAKE_CLI)

    monkeypatch.setattr(css_build.urllib.request, "urlopen", urlopen)
    output = tmp_path / "app.css"
    monkeypatch.setattr(css_build, "OUTPUT", output)
    return downloads, output


def test_fetch_downloads_the_pinned_release_once(cli):
    downloads, output = cli

    call_command("build_css", "--fetch", stdout=io.StringIO())
    call_command("build_css", "--fetch", stdout=io.StringIO())

    assert downloads == [
        f"https://github.com/tailwindlabs/tailwindcss/releases/download"
        f"/v{css_build.TAILWIND_VERSION}/tailwindcss-{css_build._platform()}"
    ]
    assert output.read_text() == ".built{}"


def test_fetch_refuses_a_download_with_the_wrong_checksum(cli, monkeypatch):
    monkeypatch.setitem(css_build.SHA256, css_build._platform(), "0" * 64)

    with pytest.raises(CommandError, match="is not Tailwind CLI"):
        call_command("build_css", "--fetch", stdout=io.StringIO())
    assert not css_build.binary_path().exists()


def test_check_exits_1_on_stale_css(cli):
    downloads, output = cli
    call_command("build_css", "--fetch", stdout=io.StringIO())
    output.write_text(".stale{}")

    with pytest.raises(CommandError, match="is out of date"):
        call_command("build_css", "--check", stdout=io.StringIO())
    assert output.read_text() == ".stale{}"


def test_check_passes_on_fresh_css(cli):
    call_command("build_css", "--fetch", stdout=io.StringIO())
    out = io.StringIO()

    call_command("build_css", "--check", stdout=out)

    assert "is up to date" in out.getvalue()


def test_the_script_and_the_command_agree(cli, capsys):
    """CI runs the script, developers the command."""
    call_command("build_css", "--fetch", stdout=io.StringIO())
    cli[1].write_text(".stale{}")

    assert css_build.main(["check"]) == 1
    assert "is out of date" in capsys.readouterr().out
