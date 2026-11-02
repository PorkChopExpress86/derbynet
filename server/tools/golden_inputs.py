"""Record which version of the shell suite the HTTP golden fixtures came from.

    uv run python tools/golden_inputs.py            # write tests/golden/inputs.sha256
    uv run python tools/golden_inputs.py --check    # exit 1 if it is out of date

`tests/golden/capture/capture-http.sh` runs scripts of `testing/` and records
what they send and get back, so editing one of them, or a file one of them
reads, can change the committed transcripts. The `golden-fixtures` workflow
catches that only after a 25-minute capture. This tool catches it in a second:
`inputs.sha256` holds the SHA-256 of every file the HTTP capture reads, and
`capture.sh` rewrites it after a full HTTP capture. `--check` (pytest, the
pre-push hook) fails when a file changed since, so recapture:

    server/tests/golden/capture.sh --http-only

Rewrite the file without a capture (no `--check`) only for a change that
cannot alter what any script sends or prints to the recorder, such as a
comment.

The inputs are every file under `tests/golden/capture/` except the engine
capture, the `testing/` scripts and files they run or read, and what those
read beside themselves (`dirname $0`/...), followed transitively. Stdlib only,
so `capture.sh` can run it with the host's python3.
"""

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTING = ROOT / "testing"
CAPTURE = ROOT / "server" / "tests" / "golden" / "capture"
MANIFEST = ROOT / "server" / "tests" / "golden" / "inputs.sha256"
# The engine fixtures don't come from the HTTP capture.
NOT_HTTP = {CAPTURE / "capture-engine.php"}

# What a capture file runs or reads from testing/: `$ROOT/testing/<path>`,
# `run_script <script>`, and `record_script <tag> <list> <steps> <script>`.
_CAPTURE_PATH = re.compile(r"\$ROOT/testing/([\w./-]+)")
_RUN = re.compile(r"^\s*(?:\w+=\S+\s+)*(run_script|record_script)\s+(.*)$", re.M)
# What a testing/ script runs or reads next to itself: `dirname $0`/<path>,
# $(dirname "${BASH_SOURCE[0]}")/<path>.
_BESIDE = re.compile(r"dirname[^/\n]*?[`)]/([\w./-]+)")


def _tracked() -> set[Path]:
    # A run of the suite leaves output.curl, cookies.curl and the like in
    # testing/; only what git tracks is an input.
    names = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", "testing", str(CAPTURE)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    return {ROOT / name for name in names if name}


TRACKED = _tracked()


def _testing_file(relative: str) -> list[Path]:
    path = (TESTING / relative).resolve()
    return [path] if path in TRACKED else []


def _named_by_capture(text: str) -> list[Path]:
    found = []
    for relative in _CAPTURE_PATH.findall(text):
        found += _testing_file(relative)
    for command, rest in _RUN.findall(text):
        words = shlex.split(rest, comments=True)
        script = words[0] if command == "run_script" else words[3]
        found += _testing_file(script)
    return found


def inputs() -> list[Path]:
    """Every file the HTTP capture reads, sorted."""
    capture = [p for p in sorted(TRACKED) if CAPTURE in p.parents]
    capture = [p for p in capture if p not in NOT_HTTP]
    seen = set(capture)
    todo = [n for p in capture for n in _named_by_capture(p.read_text())]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for relative in _BESIDE.findall(text):
            todo += _testing_file(relative)
    return sorted(seen)


def manifest_text() -> str:
    lines = (
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n"
        for p in inputs()
    )
    return "".join(lines)


def stale() -> list[str]:
    """The inputs whose hash differs from the manifest's, or that it lacks or
    no longer has."""
    want = manifest_text().splitlines()
    have = MANIFEST.read_text().splitlines() if MANIFEST.exists() else []
    return sorted({line.split("  ", 1)[1] for line in set(want) ^ set(have)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.check:
        MANIFEST.write_text(manifest_text())
        return 0
    changed = stale()
    if changed:
        print(
            "The HTTP golden fixtures were captured from other versions of:",
            *(f"  {name}" for name in changed),
            "Recapture with server/tests/golden/capture.sh --http-only"
            " (see the docstring of server/tools/golden_inputs.py).",
            sep="\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
