"""A comment that says the port does what PHP does cites where PHP does it.

plan/AGENT-BRIEF.md asks for the PHP file and line (`kiosks.inc:27-31`) or
the test beside the comment. #50's, #52's and #53's reviews each sent back
"as replay.php does" comments with neither, so this test finds them first.
It reads Markdown too, since #231's README said "shows nobody, as PHP does"
with neither (#231 retro). It reads "as PHP's ... does" and the port's own
JavaScript under `static/` too: #252's first docstring said the next drive
starts 10 s after the last, "as PHP's does", with neither, where PHP waits
about 9.2 s, and the check matched only "as PHP does" and skipped `static/`
(#252 retro).
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", "vendor", "var", "__pycache__"}
# "as PHP does", "as replay.php did", "as timer-alive.js does", a PHP
# function, "as `set_ballot_depth()` does" (#54 retro), and up to four words
# after "PHP's", a comment's `#` or `//` on the next line among them: "as
# PHP's does", "as PHP's `basename()` does" (#252 retro).
CLAIM = re.compile(
    r"\bas\s+(?:PHP(?:'s(?:\s+[\w`()./#*-]+){0,4})?|[\w./-]+\.(?:php|inc|js)"
    r"|`[\w>:-]+\(\)`)\s+(?:does|did)\b"
)
# A PHP file and line (`action.photo.delete.inc:58`, `kiosks.inc:27-31`), or a test.
CITATION = re.compile(r"[\w./-]+\.(?:php|inc|js|kiosk):\d+|\btest_\w+")
LINES_AFTER = 2

# Claims that predate the "PHP's" and `static/` reading (#252 retro), not yet
# cited, keyed by file and the claim's words. Remove an entry when you cite
# its claim; never add one.
KNOWN = {
    ("README.md", "as PHP's `basename()` does"),
    ("README.md", "as PHP's `settings.lock` file did"),
    ("awards/editing.py", "as PHP's insert does"),
    ("core/models.py", "as PHP's `history.php` does"),
    ("derbynet/settings.py", "as PHP's `settings.lock` file did"),
    ("kiosks/services.py", "as PHP's UPDATE does"),
    ("legacy_api/handlers/playlist.py", "as PHP's null does"),
    ("legacy_api/registry.py", "as PHP's does"),
    ("legacy_api/tests/test_settings_list.py", "as PHP's does"),
    ("media/models.py", "as PHP's rewritten file does"),
    ("media/videos.py", "as PHP's `basename()` does"),
    ("racing/engine/groups/aggregates.py", "as PHP's `constituents` column does"),
    ("racing/engine/tests/reschedule_replay.py", "as PHP's did"),
    ("racing/services/polls.py", "as PHP's null does"),
    ("racing/services/tests/test_master.py", "as PHP's `get_next_heat()` did"),
    ("roster/models.py", "as PHP's `COLLATE NOCASE` did"),
    ("roster/services/barcode.py", "as PHP's `trim()` does"),
    ("roster/services/groups.py", "as PHP's `COLLATE NOCASE` did"),
    ("transfer/snapshot.py", "as PHP's does"),
    ("ui/awards/views.py", "as PHP's does"),
    ("ui/dashboard/state.py", "as PHP's `query.poll.kiosk.inc` does"),
    ("ui/dashboard/views.py", "as PHP's Manual Results button does"),
    ("ui/presenter/views.py", "as PHP's does"),
    ("ui/replay/views.py", "as PHP's htmlspecialchars() does"),
    ("ui/static/ui/replay.js", "as PHP's does"),
    ("ui/tests/test_dashboard.py", "as PHP's query.poll.kiosk.inc does"),
    ("ui/tests/test_kiosk_pages.py", "as PHP's does"),
    ("ui/tests/test_kiosks.py", "as PHP's does"),
    ("ui/tests/test_poll_signatures.py", "as PHP's coordinator page does"),
    ("ui/tests/test_undo_links.py", "as PHP's does"),
}


def sources():
    for path in sorted(SERVER.rglob("*")):
        if path.suffix not in (".py", ".js", ".html", ".md") or path == Path(__file__):
            continue
        if SKIP.intersection(path.relative_to(SERVER).parts[:-1]):
            continue
        yield path


def uncited_claims(text: str) -> list[tuple[int, str]]:
    """`(line, claim)` of each claim with no citation within `LINES_AFTER`
    lines, the claim's words joined by single spaces."""
    lines = text.splitlines()
    found = []
    for match in CLAIM.finditer(text):
        first = text.count("\n", 0, match.start())
        last = text.count("\n", 0, match.end())
        near = "\n".join(lines[first : last + 1 + LINES_AFTER])
        if not CITATION.search(near):
            found.append((first + 1, " ".join(match.group().split())))
    return found


def uncited(text: str) -> list[int]:
    return [line for line, _ in uncited_claims(text)]


def tree_claims() -> list[tuple[str, int, str]]:
    return [
        (str(path.relative_to(SERVER)), line, claim)
        for path in sources()
        for line, claim in uncited_claims(path.read_text(encoding="utf-8"))
    ]


def test_a_claim_with_no_citation_is_found():
    assert uncited("# The port rounds, as PHP does.\nx = 1\n") == [1]
    assert uncited("x = 1\n// polls, as replay.php does (#53).\n") == [2]
    assert uncited("# Caps it, as `set_ballot_depth()` does.\n") == [1]
    assert uncited("# The next drive starts 10 s later, as PHP's does.\n") == [1]
    assert uncited("# as PHP's\n# `basename()` does.\n") == [1]
    assert uncited("// Starts again, as PHP's animate_car() does.\n") == [1]


def test_a_claim_with_a_file_and_line_or_a_test_passes():
    assert uncited("# Rounds, as PHP does (action.photo.delete.inc:58).\n") == []
    assert uncited("# as kiosks.inc does,\n# lines kiosks.inc:27-31\n") == []
    assert uncited('"""Rounds as PHP does; test_rounding shows it."""\n') == []
    assert uncited("// as PHP's animate_car()\n// does (scale-MPH.kiosk:11-18)\n") == []


def test_the_port_s_javascript_is_read_and_vendored_code_is_not():
    paths = {str(path.relative_to(SERVER)) for path in sources()}
    assert "ui/static/ui/kiosk-simple.js" in paths
    assert not [path for path in paths if "/vendor/" in path]


def test_every_claim_to_do_as_php_does_cites_where():
    missing = [
        f"{path}:{line}"
        for path, line, claim in tree_claims()
        if (path, claim) not in KNOWN
    ]
    assert missing == [], (
        "Cite the PHP file and line (kiosks.inc:27-31) or the test that shows it "
        f"beside each claim (plan/AGENT-BRIEF.md): {missing}"
    )


def test_each_known_claim_is_still_uncited():
    """A KNOWN entry whose claim is cited or gone is removed, so the list
    only shrinks."""
    left = {(path, claim) for path, _, claim in tree_claims()}
    assert sorted(KNOWN - left) == []
