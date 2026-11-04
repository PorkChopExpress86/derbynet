"""The vendored libraries are the pinned releases (#40)."""

import hashlib
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent
MANIFEST = (UI / "static" / "ui" / "vendor" / "VENDORED.md").read_text()
VENDORED = [
    *sorted((UI / "static" / "ui" / "vendor").glob("*.js")),
    *sorted((UI / "static" / "ui" / "vendor").glob("*.css")),
    *sorted((UI / "tests" / "browser" / "vendor").glob("*.js")),
]


def test_every_vendored_file_is_listed_with_its_checksum():
    assert len(VENDORED) == 9
    for path in VENDORED:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert path.name in MANIFEST, path.name
        assert digest in MANIFEST, path.name


def test_the_base_template_loads_the_listed_versions():
    base = (UI / "templates" / "ui" / "base.html").read_text()

    for name in re.findall(r"ui/vendor/([\w.-]+\.js)", base):
        assert (UI / "static" / "ui" / "vendor" / name).exists(), name
    assert "http" not in re.sub(r"<!--.*?-->", "", base.split("<body")[0]).replace(
        "http-equiv", ""
    )


TEMPLATES = sorted(
    path
    for app in (UI, UI.parent / "accounts")
    for path in (app / "templates").rglob("*.html")
)
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", re.I)


def test_no_template_loads_anything_from_another_site():
    """Race venues often have no internet: every script, stylesheet and link
    a page loads comes from this server."""
    assert len(TEMPLATES) > 20
    offenders = [p.name for p in TEMPLATES if EXTERNAL.search(p.read_text())]
    assert offenders == []


def test_the_scan_finds_an_external_url():
    assert EXTERNAL.search('<script src="https://cdn.example/htmx.js">')
    assert EXTERNAL.search("<link href='//cdn.example/x.css'>")
    assert not EXTERNAL.search('<script src="/static/ui/app.js">')
