"""Each PHP file and line the port cites (`vote.php:67`, `kiosks.inc:27-31`)
names a file of the PHP tree and a line with code on it. It reads the
server's sources and the testing/ scripts and READMEs, and a cite may name a
testing/ file too (`testing/puppeteer/ondeck-columns-test.js:12`): #240 cited
PHP from test-visit-each-page.sh, which this never read, and lost three
rounds to a puppeteer cite this called "no such PHP file" (#240 retro).

#54 wrote seven cites from memory of an earlier read, and three landed on a
blank line or a lone brace (`voterid.inc:34`, `vote.php:146`). This finds
those and a file that was renamed or never existed. A cite one line off onto
other code still passes, as #240's `action.replay.message.inc:3` (copied from
the lead's message; the TODO is on line 4) did: copy cites from `grep -n`, not
memory or a message.
`tests/test_php_claims.py` (#53 retro) checks that a claim has a cite at all.

A cite may name a file of the port too (`kiosk.css:353`,
`server/accounts/defaults.py:15`), and may be written in prose
(`` `kiosk.css` line 353 ``). #250 wrote "kiosk.css line 351" because this
called `kiosk.css:351` "no such PHP file", and nothing read the prose form; a
CSS edit above it had moved the rule to line 353 (#250 retro). A `.py` cite
needs a folder (`accounts/defaults.py:15`), since tests print made-up ones
(`test_late.py:3`).
"""

import re
from collections import defaultdict
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
# The PHP tree, the templates build.xml makes some of its files from, and the
# suites in testing/, whose puppeteer scripts a replacement test cites
# (`testing/puppeteer/ondeck-columns-test.js:12`; #240 retro).
# The port's own files are cited too (#250 retro).
PHP_TREES = (ROOT / "website", ROOT / "templates", ROOT / "testing", SERVER)
SUFFIXES = (".php", ".inc", ".js", ".kiosk", ".css", ".html")
SKIP = {".venv", "node_modules", "var", "__pycache__", "vendor"}
# The testing/ scripts and READMEs cite PHP too (#240's test-visit-each-page.sh).
TESTING_SUFFIXES = (".sh", ".md", ".js")
# `file:12`, `file:12-15`, or in prose `file` line 12 / `file`, lines 12-15.
WHERE = r"(?:`?:|`?,? lines? )(?P<first>\d+)(?:-(?P<last>\d+))?\b"
CITE = re.compile(
    r"(?<![\w/.-])(?P<file>(?:(?:[\w-]+/)*[\w.-]+(?:"
    + "|".join(re.escape(s) for s in SUFFIXES)
    + r")|(?:[\w-]+/)+[\w.-]+\.py))"
    + WHERE
)
# A line with no letter, digit or quote on it: blank, or a lone brace.
NO_CODE = re.compile(r"[^\w'\"$]*")


def php_files() -> dict[str, list[Path]]:
    found = defaultdict(list)
    for tree in PHP_TREES:
        for path in tree.rglob("*"):
            if (
                path.suffix in (*SUFFIXES, ".py")
                and path.is_file()
                and not SKIP.intersection(path.parts)
            ):
                found[path.name].append(path)
    return found


def problem(cite: re.Match, files: dict[str, list[Path]]) -> str | None:
    """Why the cite points nowhere, or None."""
    rel = (
        cite["file"]
        .removeprefix("website/")
        .removeprefix("templates/")
        .removeprefix("server/")
    )
    matches = [
        p for p in files.get(Path(rel).name, []) if p.as_posix().endswith("/" + rel)
    ]
    if not matches:
        return "no such file"
    first = int(cite["first"])
    last = int(cite["last"] or first)
    for path in matches:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if 0 < first <= last <= len(lines) and any(
            not NO_CODE.fullmatch(line) for line in lines[first - 1 : last]
        ):
            return None
    return "no code on that line"


def sources():
    for tree, suffixes in (
        (SERVER, (".py", ".js", ".html", ".md", ".css")),
        (ROOT / "testing", TESTING_SUFFIXES),
    ):
        for path in sorted(tree.rglob("*")):
            if path.suffix not in suffixes or path == Path(__file__):
                continue
            if SKIP.intersection(path.relative_to(tree).parts[:-1]):
                continue
            yield path


def test_a_cite_to_a_blank_line_a_brace_or_no_file_is_found():
    files = php_files()

    def check(text):
        return problem(CITE.search(text), files)

    assert check("website/inc/voterid.inc:34") == "no code on that line"
    assert check("vote.php:146") == "no code on that line"
    assert check("vote.php:99999") == "no code on that line"
    assert check("no-such-page.php:3") == "no such file"
    assert check("`website/vote.php:67`") is None
    assert check("kiosks.inc:27-31") is None
    # A replacement test cites the puppeteer script it replaces (#240 retro).
    assert check("testing/puppeteer/ondeck-columns-test.js:1") is None
    assert check("testing/puppeteer/no-such-test.js:1") == "no such file"
    # The port's own files, and the prose form (#250 retro).
    assert check("`server/accounts/defaults.py:15`") is None
    assert check("accounts/no_such_module.py:1") == "no such file"
    assert check("`kiosk.css` line 1") is None
    assert check("kiosk.css, lines 6") == "no code on that line"
    assert check("`kiosk.css` lines 1-3") is None
    assert check("ui/static/ui/kiosk.css:6") == "no code on that line"
    # A test's made-up output names no folder, and is no cite.
    assert CITE.search("test_late.py:3 gallery") is None


def test_the_testing_scripts_are_read():
    """test-visit-each-page.sh cites action.replay.message.inc (#240)."""
    read = {path.relative_to(ROOT).as_posix() for path in sources()}
    assert "testing/test-visit-each-page.sh" in read
    assert "testing/README.md" in read


def test_every_php_cite_names_a_line_of_code():
    files = php_files()
    wrong = [
        f"{path.relative_to(ROOT)}:{number}: {cite[0]}: {why}"
        for path in sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for cite in CITE.finditer(line)
        if (why := problem(cite, files))
    ]
    assert wrong == []
