"""`form_fields` in `capture-http.sh` splits a multipart capture's body into
its fields as written, and `capture` sends each one as one curl field."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "golden" / "capture" / "capture-http.sh"


def function(name: str) -> str:
    source = SCRIPT.read_text()
    start = source.index(f"{name}() {{")
    return source[start : source.index("\n}\n", start) + 3]


def form_fields(body: str, cwd: Path | None = None) -> list[str]:
    run = subprocess.run(
        ["bash", "-c", function("form_fields") + 'form_fields "$1"', "bash", body],
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


# A curl that writes an empty reply and records its arguments, one per line.
FAKE_CURL = """
curl() {
  printf '%s\\n' "$@" > "$RAW/.curl-args"
  local previous=
  for arg in "$@"; do
    case "$previous" in
      -D) printf 'HTTP/1.1 200 OK\\r\\n' > "$arg" ;;
      -o) : > "$arg" ;;
    esac
    previous="$arg"
  done
}
"""


def test_capture_sends_each_multipart_field_as_written(tmp_path):
    (tmp_path / "Bx").touch()
    script = (
        function("form_fields")
        + function("capture")
        + FAKE_CURL
        + 'RAW="$PWD"; CAPTURE_FILE="$RAW/t.http"; CAPTURE_BODY="$RAW/.body"\n'
        + "declare -A CAPTURE_SEQS=([$CAPTURE_FILE]=0)\n"
        + 'CAPTURE_UPLOAD=file=photo.jpg capture upload "POST action.php $1"\n'
    )
    subprocess.run(
        ["bash", "-c", script, "bash", "name=A B*&key=* ?&empty="],
        cwd=tmp_path,
        check=True,
    )
    args = (tmp_path / ".curl-args").read_text().splitlines()
    fields = [args[i + 1] for i, arg in enumerate(args) if arg == "--form-string"]
    assert fields == ["name=A B*", "key=* ?", "empty="]
