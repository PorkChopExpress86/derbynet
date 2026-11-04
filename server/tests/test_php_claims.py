"""A sentence about what PHP does names its evidence (#49 retro).

#49's review found three such sentences with nothing behind them, such as
"as PHP skips" blank rows, and five UI differences from PHP with no id. The
brief (`plan/AGENT-BRIEF.md`) asks for both; this test checks the wording it
can see, in the paragraph of a comment or docstring:

- a claim that the port acts "as PHP does", or as a named PHP file does,
  cites a `website/` or `templates/` line, a `testing/` script, a run
  (`php-scratch`, "run with"), the golden capture, a test by name, or an id;
- under `ui/`, a difference ("where PHP", "unlike PHP", "PHP never") cites
  an id (`D69`, `Q24`, `S13`, `C50`) or a `website/` line.

A wording the patterns miss is past its reach; the review still looks.
"""

import re
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
SKIPPED = {".venv", "tests", "migrations"}

CLAIM = re.compile(
    r"\bas (?:PHP|`?[\w./-]+\.(?:php|js|inc|kiosk)`?)(?:'s)? (?:does|did|do"
    r"|polls|skips|shows|prints|sends|reads|writes|answers|stores|keeps)\b"
)
DIFFERENCE = re.compile(r"\b(?:where PHP|[Uu]nlike PHP|PHP never)\b")
ID = r"\b[DQSC]\d+\b"
CLAIM_EVIDENCE = re.compile(
    ID + r"|website/|templates/\w|testing/|php-scratch|run with|golden|\btest_\w+"
)
DIFFERENCE_EVIDENCE = re.compile(ID + r"|website/")


def _paragraph(lines: list[str], i: int) -> str:
    """The lines around line `i` up to a blank line or a `def`/`class`."""
    start = i
    while (
        start > 0
        and lines[start - 1].strip()
        and not lines[start - 1].lstrip().startswith(("def ", "class ", "@"))
    ):
        start -= 1
    end = i
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return "\n".join(lines[start : end + 1])


def offenders(source: str, *, ui: bool) -> list[str]:
    lines = source.splitlines()
    found = []
    for i, line in enumerate(lines):
        rules = [(CLAIM, CLAIM_EVIDENCE)]
        if ui:
            rules.append((DIFFERENCE, DIFFERENCE_EVIDENCE))
        for pattern, evidence in rules:
            if pattern.search(line) and not evidence.search(_paragraph(lines, i)):
                found.append(f"{i + 1}: {line.strip()}")
    return found


FILES = sorted(
    path
    for path in SERVER.rglob("*.py")
    if not SKIPPED & set(path.relative_to(SERVER).parts)
)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SERVER)))
def test_a_sentence_about_php_names_its_evidence(path):
    ui = path.relative_to(SERVER).parts[0] == "ui"
    assert offenders(path.read_text(), ui=ui) == [], (
        "Cite the PHP line (`website/...:N`), the run (`php-scratch.sh`), the"
        " test, or the D/Q/S/C id in the same paragraph, or drop the claim."
    )


UNCITED_CLAIM = '''
def parse_rows(kind, data):
    """All-blank rows are left out, as PHP skips them."""
'''

CITED_CLAIM = '''
def parse_rows(kind, data):
    """All-blank rows are left out, as PHP's page skips them
    (`website/js/import-csv.js:374` to `:388`)."""
'''

UNCITED_DIFFERENCE = '''
def legacy_page(request):
    """Anything else is the racer photos, where PHP fails on a bad name."""
'''

CITED_DIFFERENCE = '''
def page(request):
    """A junk racerid shows every racer, where PHP put it into SQL (S13)."""
'''


def test_an_uncited_claim_is_flagged():
    assert len(offenders(UNCITED_CLAIM, ui=False)) == 1


def test_an_uncited_ui_difference_is_flagged():
    assert len(offenders(UNCITED_DIFFERENCE, ui=True)) == 1
    assert offenders(UNCITED_DIFFERENCE, ui=False) == []


@pytest.mark.parametrize("source", [CITED_CLAIM, CITED_DIFFERENCE])
def test_a_cited_sentence_passes(source):
    assert offenders(source, ui=True) == []
