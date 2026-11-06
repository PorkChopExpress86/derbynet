"""Each PHP file and line the port cites (`vote.php:67`, `kiosks.inc:27-31`)
names a file of the PHP tree and a line with code on it.

#54 wrote seven cites from memory of an earlier read, and three landed on a
blank line or a lone brace (`voterid.inc:34`, `vote.php:146`). This finds
those and a file that was renamed or never existed. A cite one line off onto
other code still passes: copy cites from `grep -n`, not memory.
`tests/test_php_claims.py` (#53 retro) checks that a claim has a cite at all.
"""

import re
from collections import defaultdict
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
# The PHP tree, and the templates build.xml makes some of its files from.
PHP_TREES = (ROOT / "website", ROOT / "templates")
SUFFIXES = (".php", ".inc", ".js", ".kiosk", ".css")
SKIP = {".venv", "node_modules", "var", "__pycache__", "vendor"}
CITE = re.compile(
    r"(?<![\w/.-])(?P<file>(?:[\w-]+/)*[\w.-]+(?:"
    + "|".join(re.escape(s) for s in SUFFIXES)
    + r")):(?P<first>\d+)(?:-(?P<last>\d+))?\b"
)
# A line with no letter, digit or quote on it: blank, or a lone brace.
NO_CODE = re.compile(r"[^\w'\"$]*")


def php_files() -> dict[str, list[Path]]:
    found = defaultdict(list)
    for tree in PHP_TREES:
        for path in tree.rglob("*"):
            if path.suffix in SUFFIXES and path.is_file():
                found[path.name].append(path)
    return found


def problem(cite: re.Match, files: dict[str, list[Path]]) -> str | None:
    """Why the cite points nowhere, or None."""
    rel = cite["file"].removeprefix("website/").removeprefix("templates/")
    matches = [
        p for p in files.get(Path(rel).name, []) if p.as_posix().endswith("/" + rel)
    ]
    if not matches:
        return "no such PHP file"
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
    for path in sorted(SERVER.rglob("*")):
        if path.suffix not in (".py", ".js", ".html", ".md") or path == Path(__file__):
            continue
        if SKIP.intersection(path.relative_to(SERVER).parts[:-1]):
            continue
        yield path


def test_a_cite_to_a_blank_line_a_brace_or_no_file_is_found():
    files = php_files()

    def check(text):
        return problem(CITE.search(text), files)

    assert check("website/inc/voterid.inc:34") == "no code on that line"
    assert check("vote.php:146") == "no code on that line"
    assert check("vote.php:99999") == "no code on that line"
    assert check("no-such-page.php:3") == "no such PHP file"
    assert check("`website/vote.php:67`") is None
    assert check("kiosks.inc:27-31") is None


def test_every_php_cite_names_a_line_of_code():
    files = php_files()
    wrong = [
        f"{path.relative_to(SERVER)}:{number}: {cite[0]}: {why}"
        for path in sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for cite in CITE.finditer(line)
        if (why := problem(cite, files))
    ]
    assert wrong == []
