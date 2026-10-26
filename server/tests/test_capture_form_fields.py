"""`form_fields` in `capture-http.sh` splits a multipart capture's body into
its fields as written."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "golden" / "capture" / "capture-http.sh"


def form_fields(body: str, cwd: Path | None = None) -> list[str]:
    source = SCRIPT.read_text()
    start = source.index("form_fields() {")
    function = source[start : source.index("\n}\n", start) + 3]
    run = subprocess.run(
        ["bash", "-c", function + 'form_fields "$1"', "bash", body],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout.splitlines()


def test_spaces_and_glob_characters_stay_in_their_field(tmp_path):
    (tmp_path / "Bx").touch()
    assert form_fields("name=A B*&key=* ?&empty=", tmp_path) == [
        "name=A B*",
        "key=* ?",
        "empty=",
    ]


def test_an_empty_body_has_no_fields():
    assert form_fields("") == []
