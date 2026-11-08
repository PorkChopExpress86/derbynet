"""A comment that says the port does what PHP does cites where PHP does it.

plan/AGENT-BRIEF.md asks for the PHP file and line (`kiosks.inc:27-31`) or
the test beside the comment. #50's, #52's and #53's reviews each sent back
"as replay.php does" comments with neither, so this test finds them first.
It reads Markdown too, since #231's README said "shows nobody, as PHP does"
with neither (#231 retro).
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", "vendor", "var", "__pycache__", "static"}
# "as PHP does", "as replay.php did", "as timer-alive.js does", and a PHP
# function, "as `set_ballot_depth()` does" (#54 retro).
CLAIM = re.compile(
    r"\bas\s+(?:PHP|[\w./-]+\.(?:php|inc|js)|`[\w>:-]+\(\)`)\s+(?:does|did)\b"
)
# A PHP file and line (`action.photo.delete.inc:58`, `kiosks.inc:27-31`), or a test.
CITATION = re.compile(r"[\w./-]+\.(?:php|inc|js|kiosk):\d+|\btest_\w+")
LINES_AFTER = 2


def sources():
    for path in sorted(SERVER.rglob("*")):
        if path.suffix not in (".py", ".js", ".html", ".md") or path == Path(__file__):
            continue
        if SKIP.intersection(path.relative_to(SERVER).parts[:-1]):
            continue
        yield path


def uncited(text: str) -> list[int]:
    lines = text.splitlines()
    found = []
    for match in CLAIM.finditer(text):
        first = text.count("\n", 0, match.start())
        last = text.count("\n", 0, match.end())
        near = "\n".join(lines[first : last + 1 + LINES_AFTER])
        if not CITATION.search(near):
            found.append(first + 1)
    return found


def test_a_claim_with_no_citation_is_found():
    assert uncited("# The port rounds, as PHP does.\nx = 1\n") == [1]
    assert uncited("x = 1\n// polls, as replay.php does (#53).\n") == [2]
    assert uncited("# Caps it, as `set_ballot_depth()` does.\n") == [1]


def test_a_claim_with_a_file_and_line_or_a_test_passes():
    assert uncited("# Rounds, as PHP does (action.photo.delete.inc:58).\n") == []
    assert uncited("# as kiosks.inc does,\n# lines kiosks.inc:27-31\n") == []
    assert uncited('"""Rounds as PHP does; test_rounding shows it."""\n') == []


def test_every_claim_to_do_as_php_does_cites_where():
    missing = [
        f"{path.relative_to(SERVER)}:{line}"
        for path in sources()
        for line in uncited(path.read_text(encoding="utf-8"))
    ]
    assert missing == [], (
        "Cite the PHP file and line (kiosks.inc:27-31) or the test that shows it "
        f"beside each claim (plan/AGENT-BRIEF.md): {missing}"
    )
