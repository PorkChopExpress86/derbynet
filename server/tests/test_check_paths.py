"""`server/docs/adr/check-paths.sh --docs`, the guides' check in CI (#61).

Each rule gets a guide that breaks only it and must fail, beside a clean guide
that must pass: a broken relative link, a missing image, a backticked path
that names no file, an anchor no heading makes, a remote image, a long dash,
and each instruction from a PHP install (`FORBIDDEN`). An anchor is checked
against the ids the docs build itself gives the headings (`ui.docs.build`),
which differ from GitHub's for a dash between words and a repeated heading.
The last test runs the check on the repository's own `docs/`.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "server" / "docs" / "adr" / "check-paths.sh"

CLEAN = """# A guide

See [the other guide](other.md#its-part) and [below](#a-part).
![A picture](img/guide/picture.png)
<img src="img/guide/picture.png" alt="Again">
Run `docker run -e TZ=America/Chicago image` and open `https://localhost/`.
The code is in `server/real.py`, and `real.php` is a website file.

## A part

Text.
"""


def run(*args, root=None):
    # The Python running the tests has the markdown package the build uses.
    env = {"PATH": "/usr/bin:/bin", "CHECK_PATHS_PYTHON": sys.executable}
    if root is not None:
        env["CHECK_PATHS_ROOT"] = str(root)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def fixture(tmp_path):
    """A repository root with `server/real.py` and `website/real.php`, and a
    docs folder holding a clean guide, another guide and its image."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "real.py").write_text("")
    (tmp_path / "website").mkdir()
    (tmp_path / "website" / "real.php").write_text("")
    docs = tmp_path / "docs"
    (docs / "img" / "guide").mkdir(parents=True)
    (docs / "img" / "guide" / "picture.png").write_bytes(b"png")
    (docs / "other.md").write_text("# Other\n\n## Its part\n")
    (docs / "guide.md").write_text(CLEAN)

    def check(text=None):
        if text is not None:
            (docs / "guide.md").write_text(text)
        return run("--docs", str(docs), root=tmp_path)

    return check


def test_a_clean_guide_passes(fixture):
    result = fixture()
    assert result.returncode == 0, result.stdout
    assert "0 failures" in result.stdout


@pytest.mark.parametrize(
    ("old", "new", "report"),
    [
        ("(other.md#its-part)", "(gone.md)", "MISSING  link gone.md"),
        (
            "(img/guide/picture.png)",
            "(img/guide/gone.png)",
            "MISSING  link img/guide/gone.png",
        ),
        (
            '<img src="img/guide/picture.png"',
            '<img src="img/guide/gone.png"',
            "MISSING  link img/guide/gone.png",
        ),
        ("`server/real.py`", "`server/gone.py`", "MISSING  server/gone.py"),
        ("`real.php`", "`gone.php`", "MISSING  gone.php"),
        ("other.md#its-part", "other.md#no-part", "MISSING  anchor other.md#no-part"),
        ("(#a-part)", "(#no-part)", "MISSING  anchor guide.md#no-part"),
        (
            "(img/guide/picture.png)",
            "(https://example.org/p.png)",
            "REMOTE IMAGE",
        ),
        (
            '<img src="img/guide/picture.png"',
            '<img src="http://example.org/p.png"',
            "REMOTE IMAGE",
        ),
        ("Text.", "Text — more.", "LONG DASH"),
        ("Text.", "Pages 1–2.", "LONG DASH"),
    ],
)
def test_each_rule_fails_a_guide_that_breaks_it(fixture, old, new, report):
    assert old in CLEAN
    result = fixture(CLEAN.replace(old, new))
    assert result.returncode == 1
    assert report in result.stdout
    assert "1 failures" in result.stdout


@pytest.mark.parametrize(
    "words",
    [
        "Edit local/config-roles.inc.",
        "Raise the limit in php.ini.",
        "Make an odbc data source.",
        "Install the derbynet.deb file.",
        "Install derbynet-server with apt.",
        "Install derbynet-extras.",
        "Open DerbyNet.dmg.",
        "Open the DerbyNet.pkg installer.",
        "Run DerbyNet.msi.",
        "Start UniServer.",
        "Start XAMPP.",
        "Pull jeffpiazza/derbynet_server.",
    ],
)
def test_each_php_install_instruction_fails(fixture, words):
    result = fixture(CLEAN.replace("Text.", words))
    assert result.returncode == 1
    assert "PHP INSTALL" in result.stdout


HEADINGS = (
    CLEAN
    + """
## Roles - passwords

## Same

## Same
"""
)


@pytest.mark.parametrize(
    ("link", "passes"),
    [
        # The build's toc rule makes one dash of a run of dashes and spaces.
        ("#roles-passwords", True),
        # GitHub's rule, one dash per space, is not a page's id.
        ("#roles---passwords", False),
        ("#same", True),
        # The build numbers a repeated heading with `_1`; GitHub uses `-1`.
        ("#same_1", True),
        ("#same-1", False),
    ],
)
def test_an_anchor_is_one_the_docs_build_makes(fixture, link, passes):
    from ui.docs.build import anchors

    assert (link[1:] in anchors(HEADINGS)) is passes
    result = fixture(HEADINGS.replace("(#a-part)", f"({link})"))
    assert result.returncode == (0 if passes else 1), result.stdout
    if not passes:
        assert f"MISSING  anchor guide.md{link}" in result.stdout


def test_an_anchor_in_another_guide_is_one_the_build_makes(fixture, tmp_path):
    (tmp_path / "docs" / "other.md").write_text("# Other\n\n## Its - part\n")
    result = fixture()
    assert "MISSING  anchor other.md#its-part" not in result.stdout
    assert result.returncode == 0, result.stdout
    result = fixture(CLEAN.replace("other.md#its-part", "other.md#its---part"))
    assert "MISSING  anchor other.md#its---part" in result.stdout


def test_the_build_gives_the_headings_the_ids_it_reports():
    from ui.docs.build import anchors, render

    _, html = render(HEADINGS)
    assert anchors(HEADINGS) == re.findall(r'<h[1-6] id="([^"]+)"', html)


def test_a_command_in_backticks_is_not_a_path_in_a_guide(fixture):
    """The clean guide holds `docker run -e TZ=America/Chicago image` and a
    URL; neither is reported."""
    result = fixture()
    assert "America/Chicago" not in result.stdout
    assert "localhost" not in result.stdout


def test_an_adr_path_with_a_space_is_still_checked(tmp_path):
    """The ADRs cite `docs/Sharing a Database With GPRM.fodt`, so a span with
    a space is a path there."""
    adrs = tmp_path / "adr"
    adrs.mkdir()
    (adrs / "0001-a.md").write_text("# A\n\nSee `docs/A Missing File.fodt`.\n")
    shutil.copy(SCRIPT, adrs / "check-paths.sh")
    result = subprocess.run(
        ["bash", str(adrs / "check-paths.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CHECK_PATHS_ROOT": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 1
    assert "MISSING  docs/A Missing File.fodt" in result.stdout


def test_the_repository_guides_pass():
    result = run("--docs", str(REPO / "docs"))
    assert result.returncode == 0, result.stdout
