"""A plan tool's comments and docstrings credit no ruling to the lead.

A reader of the code sees no message from the lead, so the text states the
reason, or cites the ticket's own words or a decision (`D10`). A ticket
number beside "the lead's choice" does not do that: #470's `runners()`
docstring said "(#470's oracle sweep, the lead's choice)", which
`docstring_claims.py --check` passes in a PR body because of the `#470`, and
#446 credited the lead in its body too (#470 retro). Here no cite clears it.
"""

import re
import subprocess
from pathlib import Path

from tools.docstring_claims import BACKTICKS, LEAD, PROSPECTIVE, QUOTED

ROOT = Path(__file__).resolve().parents[2]
# The plan's tools and hooks, where #470's sentence was.
TOOLS = ["plan/bin", "plan/*.py", "server/tools", ".githooks"]
# A line's comment mark and indent, so a sentence reads across lines.
MARK = re.compile(r"^[ \t]*(?:#+[ \t]?)?", re.M)


def rulings(text: str) -> list[str]:
    """Each place `text` credits a ruling to the lead, outside quotes and
    code spans."""
    text = MARK.sub("", text)
    for pattern in (QUOTED, BACKTICKS, PROSPECTIVE):
        text = pattern.sub(lambda m: " " * len(m.group()), text)
    return [" ".join(m.group().split()) for m in LEAD.finditer(text)]


def tool_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", *TOOLS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ROOT / name for name in listed if (ROOT / name).is_file()]


def test_no_tool_credits_a_ruling_to_the_lead():
    found = [
        f"{path.relative_to(ROOT)}: {hit}"
        for path in tool_files()
        for hit in rulings(path.read_text(errors="replace"))
    ]
    assert not found, (
        "a tool credits a ruling to the lead; state the reason, or cite the"
        " ticket's words or a decision:\n" + "\n".join(found)
    )


def test_470s_docstring_is_named():
    docstring = (
        "    map the shell scripts test_round_scenarios.py names, which no kiosk\n"
        '    test reads (#470\'s oracle sweep, the lead\'s choice)."""\n'
    )
    assert rulings(docstring) == ["the lead's choice"]


def test_a_sentence_across_comment_lines_is_named():
    assert rulings("# as the lead\n# chose for it") == ["the lead chose"]


def test_a_quoted_example_or_a_call_still_open_is_not_named():
    assert rulings('# a body that says "the lead\'s choice" is named') == []
    assert rulings("# which is the lead's call") == []
