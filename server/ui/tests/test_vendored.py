"""The vendored libraries are the pinned releases (#40)."""

import hashlib
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent
MANIFEST = (UI / "static" / "ui" / "vendor" / "VENDORED.md").read_text()
VENDORED = [
    *sorted((UI / "static" / "ui" / "vendor").glob("*.js")),
    *sorted((UI / "tests" / "browser" / "vendor").glob("*.js")),
]


def test_every_vendored_file_is_listed_with_its_checksum():
    assert len(VENDORED) == 4
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
