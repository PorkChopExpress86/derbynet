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
CSS edit above it had moved the rule to line 353 (#250 retro). The browser
timer's sources under timer/ are read as cite targets too
(`timer/derbynet-timer/src/js/host_poller.js:216`, #307 retro). A `.py` cite
needs a folder (`accounts/defaults.py:15`), since tests print made-up ones
(`test_late.py:3`).

A cite whose lines are all comments names the comment above the code, not
the code: #494 cited `now-racing.js:11`, the comment over the linger call on
line 12, and review found it. A comment is a line comment (`//` in
JavaScript, PHP and kiosk files, `#` in PHP, Python and kiosk files) or
inside a block comment (`/* */`, and `<!-- -->` in HTML and kiosk files),
middle lines included. A string that holds `/*` or `//` reads as a comment
too, and a Python docstring reads as code. A comment is cited on purpose
with "comment", "commented" or "TODO" within a few words of the cite
(`action.photo.rotate.inc:5`, its header comment).
"""

import re
from collections import defaultdict
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
# The PHP tree, the templates build.xml makes some of its files from, and the
# suites in testing/, whose puppeteer scripts a replacement test cites
# (`testing/puppeteer/ondeck-columns-test.js:12`; #240 retro).
# The port's own files are cited too (#250 retro), and the browser timer the
# port serves unchanged (ADR 0011), whose cite #307 had to rewrite as a
# function name because this called it "no such file" (#307 retro).
PHP_TREES = (
    ROOT / "website",
    ROOT / "templates",
    ROOT / "testing",
    ROOT / "timer",
    SERVER,
)
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
# Comments by the cited file's suffix (#494 retro): block comments, and
# what starts a line comment.
SLASH_STAR = r"/\*.*?\*/"
ANGLE = r"<!--.*?-->"
BLOCK = {
    ".php": SLASH_STAR,
    ".inc": SLASH_STAR,
    ".js": SLASH_STAR,
    ".css": SLASH_STAR,
    ".html": ANGLE,
    ".kiosk": SLASH_STAR + "|" + ANGLE,
}
LINE_COMMENT = {
    ".php": ("//", "#"),
    ".inc": ("//", "#"),
    ".js": ("//",),
    ".py": ("#",),
    ".kiosk": ("//", "#"),
}
# Near the cite, the citing text says it cites a comment: up to 40
# characters before it or 20 after.
NAMES_A_COMMENT = re.compile(r"\b[Cc]omment(?:s|ed)?\b|\bTODO\b")


def comment_lines(text: str, suffix: str) -> set[int]:
    """The 1-based numbers of the lines of `text` that hold only comments."""
    block = BLOCK.get(suffix)
    if block:
        text = re.sub(
            block, lambda m: re.sub(r"[^\n]", " ", m[0]), text, flags=re.DOTALL
        )
    starts = LINE_COMMENT.get(suffix, ())
    return {
        number
        for number, line in enumerate(text.splitlines(), 1)
        if not line.strip() or line.lstrip().startswith(starts)
    }


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


def problem(cite: re.Match, files: dict[str, list[Path]], line: str = "") -> str | None:
    """Why the cite points nowhere, or None. `line` is the citing line."""
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
    why = "no code on that line"
    near = line[max(cite.start() - 40, 0) : cite.end() + 20]
    for path in matches:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        cited = [
            n
            for n in range(first, last + 1)
            if n <= len(lines) and not NO_CODE.fullmatch(lines[n - 1])
        ]
        if not 0 < first <= last <= len(lines) or not cited:
            continue
        if set(cited) <= comment_lines(text, path.suffix):
            if NAMES_A_COMMENT.search(near):
                return None
            why = "a comment, not code (cite the code, or say comment)"
            continue
        return None
    return why


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
        return problem(CITE.search(text), files, text)

    assert check("website/inc/voterid.inc:34") == "no code on that line"
    assert check("vote.php:146") == "no code on that line"
    assert check("vote.php:99999") == "no code on that line"
    assert check("vote.php:0") == "no code on that line"
    assert check("vote.php:67-99999") == "no code on that line"
    assert check("no-such-page.php:3") == "no such file"
    assert check("`website/vote.php:67`") is None
    assert check("kiosks.inc:27-31") is None
    # A replacement test cites the puppeteer script it replaces (#240 retro).
    assert check("testing/puppeteer/ondeck-columns-test.js:1") is None
    # The browser timer the port serves unchanged (#307 retro).
    assert check("timer/derbynet-timer/src/js/host_poller.js:216-222") is None
    assert check("timer/derbynet-timer/src/js/no_such_poller.js:1") == "no such file"
    assert check("testing/puppeteer/no-such-test.js:1") == "no such file"
    # The port's own files, and the prose form (#250 retro).
    assert check("`server/accounts/defaults.py:15`") is None
    assert check("accounts/no_such_module.py:1") == "no such file"
    assert check("`kiosk.css` line 7") is None
    assert check("kiosk.css, lines 6") == "no code on that line"
    assert check("`kiosk.css` lines 6-8") is None
    assert (
        check("`kiosk.css` line 1")
        == "a comment, not code (cite the code, or say comment)"
    )
    assert check("ui/static/ui/kiosk.css:6") == "no code on that line"
    # A comment line, unless the citing line says so (#494 retro).
    comment = "a comment, not code (cite the code, or say comment)"
    assert check("website/js/now-racing.js:11") == comment
    assert check("website/js/now-racing.js:11-12") is None
    assert check("website/js/now-racing.js:6-7") == comment
    assert check("its TODO, now-racing.js:6-7") is None
    assert check("the header comment, `action.photo.rotate.inc:5`") is None
    # The word is near the cite, so it excuses no other cite on the line;
    # TODO is matched in capitals, so "mastodon" is no TODO (#512 review).
    far = "a comment at `action.photo.rotate.inc:5`, and code at "
    line = far + "x" * 40 + " website/js/now-racing.js:11"
    assert problem(CITE.search(line, len(far) + 40), files, line) == comment
    assert check("mastodon, now-racing.js:11") == comment
    # A block comment's middle line, with no `*` (#512 review).
    assert check("`kiosk.css` line 3") == comment


def test_comment_lines_are_read_by_suffix():
    """Line and block comments, a block's middle lines too (#512 review)."""
    js = "a();\n/* one\n   two */\nb(); // c\n// d\nx = 2 *\n  * 2;\n/* e */ f();\n"
    assert comment_lines(js, ".js") == {2, 3, 5}
    html = "<p>\n<!-- a\nb -->\n<p>\n"
    assert comment_lines(html, ".html") == {2, 3}
    php = "<?php\n# a\n/*\n b\n*/\n$c = 1;\n"
    assert comment_lines(php, ".php") == {2, 3, 4, 5}
    assert comment_lines("a {\n/* b\n c */\n}\n", ".css") == {2, 3}
    assert comment_lines("# a\nb = 1\n", ".py") == {1}
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
        if (why := problem(cite, files, line))
    ]
    assert wrong == []
