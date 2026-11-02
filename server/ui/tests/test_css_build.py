import pytest

from ui import css_build


def test_a_binary_with_the_wrong_checksum_is_refused(tmp_path):
    fake = tmp_path / "tailwindcss"
    fake.write_bytes(b"#!/bin/sh\necho not tailwind\n")

    with pytest.raises(css_build.BuildError, match="is not Tailwind CLI 4.1.11"):
        css_build.verify(fake, "linux-x64")


def test_the_build_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("DERBYNET_TAILWIND", raising=False)
    monkeypatch.setattr(
        css_build.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded")
    )

    with pytest.raises(css_build.BuildError, match="build_css --fetch"):
        css_build.build(tmp_path / "out.css")


def test_every_release_platform_is_pinned():
    assert set(css_build.SHA256) == {
        "linux-arm64",
        "linux-arm64-musl",
        "linux-x64",
        "linux-x64-musl",
        "macos-arm64",
        "macos-x64",
        "windows-x64.exe",
    }
    assert all(len(digest) == 64 for digest in css_build.SHA256.values())


@pytest.mark.skipif(
    not css_build.binary_path().exists(), reason="the Tailwind CLI is not fetched"
)
def test_the_committed_css_is_up_to_date():
    """CI's ui job runs the same check, `python -I ui/css_build.py check`, in a
    container with no network and no Node."""
    assert css_build.check()


def test_the_committed_css_holds_the_templates_classes():
    """A source path that matches nothing builds only Tailwind's preflight."""
    css = css_build.OUTPUT.read_text()

    for utility in (".sr-only", ".bg-switch-off", ".lg\\:flex", ".size-11"):
        assert utility in css, utility
