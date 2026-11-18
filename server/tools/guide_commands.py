"""The commands the guides in `docs/` tell the user to run, and the registry
that says, for each one, which check runs it or why none can (#414).

Data shape: a `Command` is (guide, line, heading, text), read from a fenced
block of a shell language. The registry, `server/docs/guide-commands.tsv`, is a
table of `Entry` rows keyed by (guide, heading, text), each a `stage` (a stage
of `server/docker/ci/guide-commands.sh`, which gets its commands from this
module), a `workflow` (a live step of that workflow's file holds the command
as a line) or a `reason`. The line is no part of the key, so an edit above a
command moves nothing; only the error names it.

    python -m tools.guide_commands            the check: exit 1 and name each
                                              command with no entry, each entry
                                              with no command
    python -m tools.guide_commands stage NAME the commands registered to
                                              stage NAME, in guide order, as
                                              NUL-separated `guide:line`, text

Fences are the ones the docs build finds (`ui.docs.build.fenced_blocks`), so
this check keeps no copy of that rule. A command is one non-blank line that is
no comment (a trailing backslash joins the next line); in a `console` block it
is a line after `$ `, and the other lines are output.
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ui.docs.build import SOURCE, fenced_blocks

SERVER = Path(__file__).resolve().parents[1]
REGISTRY = SERVER / "docs" / "guide-commands.tsv"

# The fenced languages whose lines are commands. `powershell` is here though
# the ticket names only the four shells: the Windows restore in
# install-docker.md is a command a reader runs, and #61's review found its
# PowerShell wrong.
LANGUAGES = frozenset({"sh", "bash", "shell", "console", "powershell", "pwsh"})
KINDS = ("stage", "workflow", "reason")
HEADING = re.compile(r"^#{1,6}[ \t]+(?P<title>.+?)[ \t#]*$")
PROMPT = "$ "


@dataclass(frozen=True)
class Command:
    guide: str  # the file name, `install-docker.md`
    line: int  # of its first line, from 1
    heading: str  # the nearest heading above it
    text: str
    n: int = 1  # the command's turn among those with this text under this heading

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.guide, self.heading, self.text, self.n)

    def named(self) -> str:
        turn = f", occurrence {self.n}" if self.n > 1 else ""
        return (
            f"docs/{self.guide}:{self.line}: `{self.text}` "
            f"(under '{self.heading}'{turn})"
        )


@dataclass(frozen=True)
class Entry:
    line: int  # in the registry
    guide: str
    heading: str
    text: str
    n: int
    kind: str
    detail: str

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.guide, self.heading, self.text, self.n)


def complete(text: str) -> bool:
    """Whether bash takes `text` as whole commands: a line that opens a quote,
    a `{`, an `if` or a here-document is not complete until the line that
    closes it. Bash itself says so (`-n` parses and runs nothing), so this
    keeps no list of constructs. At the end of its input bash accepts a
    here-document with a warning, which is how an open one shows."""
    done = subprocess.run(
        ["bash", "-n", "-c", text], capture_output=True, text=True, check=False
    )
    return done.returncode == 0 and "here-document" not in done.stderr


def split(language: str, code: str, first_line: int) -> list[tuple[int, str]]:
    """(line, command) for each command of one block's code. A construct over
    several lines is one command, its lines joined with newlines; a line that
    no run of the block's lines completes raises ValueError naming it."""
    lines = code.split("\n")
    shell = language != "powershell" and language != "pwsh"
    found, i = [], 0
    while i < len(lines):
        start, text = i, lines[i].strip()
        i += 1
        if language == "console":
            if not text.startswith(PROMPT):
                continue
            text = text[len(PROMPT) :].strip()
        if not text or text.startswith("#"):
            continue
        while text.endswith("\\") and i < len(lines):
            text = f"{text[:-1].rstrip()} {lines[i].strip()}".strip()
            i += 1
        while shell and not complete(text):
            if i >= len(lines) or (
                language == "console" and lines[i].startswith(PROMPT)
            ):
                raise ValueError(
                    f"line {first_line + start}: `{text.splitlines()[0]}` opens a "
                    "construct that no line of its block closes, so it is no command"
                )
            text = f"{text}\n{lines[i]}"
            i += 1
        found.append((first_line + start, text))
    return found


def headings(text: str, blocks: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """(line, title) of each heading outside the fenced blocks, whose
    (first, last) line spans are `blocks`."""
    found = []
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if any(first <= number <= last for first, last in blocks):
            continue
        if match := HEADING.match(line):
            found.append((number, match.group("title")))
    return found


def commands(guide: str, text: str) -> list[Command]:
    """Every command of the guide's shell blocks, in order."""
    blocks = list(fenced_blocks(text))
    spans = [
        (first - 1, first + code.count("\n")) for _, first, code in blocks
    ]  # the fences' own lines too
    titles = headings(text, spans)
    found = []
    for language, first, code in blocks:
        if language not in LANGUAGES:
            continue
        title = next((t for n, t in reversed(titles) if n < first), "")
        try:
            pairs = split(language, code, first)
        except ValueError as error:
            raise ValueError(f"docs/{guide}: {error}") from error
        for line, command in pairs:
            turn = 1 + sum(c.key[:3] == (guide, title, command) for c in found)
            found.append(Command(guide, line, title, command, turn))
    return found


def guide_commands(folder: Path = SOURCE) -> list[Command]:
    """Every guide's commands, guides in name order."""
    return [
        command
        for path in sorted(folder.glob("*.md"))
        for command in commands(path.name, path.read_text())
    ]


def encode(text: str) -> str:
    """A command as a registry cell: a backslash is `\\\\`, a newline `\\n`."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def decode(cell: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), cell)


def parse_registry(text: str) -> list[Entry]:
    """The table's rows: `guide<TAB>heading<TAB>turn<TAB>command<TAB>kind
    detail`, where `turn` counts the commands with this text under this heading
    in the guide, from 1. A blank line and a line starting with `#` are no row."""
    entries = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != 5 or not cells[2].isdecimal() or int(cells[2]) < 1:
            raise ValueError(
                f"guide-commands.tsv:{number}: expected 5 tab-separated cells "
                "with a turn from 1 in the third, not "
                f"{len(cells)} cells with {cells[2:3]!r}"
            )
        guide, heading, turn, command, entry = cells
        kind, _, detail = entry.partition(" ")
        if kind not in KINDS or not detail.strip():
            raise ValueError(
                f"guide-commands.tsv:{number}: the last cell is one of "
                f"{', '.join(KINDS)} and what it names, not {entry!r}"
            )
        entries.append(
            Entry(
                number,
                guide,
                decode(heading),
                decode(command),
                int(turn),
                kind,
                detail.strip(),
            )
        )
    seen: dict[tuple[str, str, str, int], int] = {}
    for entry in entries:
        if entry.key in seen:
            raise ValueError(
                f"guide-commands.tsv:{entry.line}: the same command as line "
                f"{seen[entry.key]}, `{entry.text}` under '{entry.heading}' in "
                f"{entry.guide}, occurrence {entry.n}"
            )
        seen[entry.key] = entry.line
    return entries


def problems(found: list[Command], entries: list[Entry]) -> list[str]:
    """Each command with no entry and each entry whose command is gone."""
    listed = {entry.key for entry in entries}
    present = {command.key for command in found}
    out = [
        f"{command.named()} has no entry in server/docs/guide-commands.tsv: "
        "add `stage <name>` for the stage of server/docker/ci/guide-commands.sh that "
        "runs it, `workflow <file>` for a workflow step that holds it, or "
        "`reason <why no check can run it>`"
        for command in found
        if command.key not in listed
    ]
    out += [
        f"server/docs/guide-commands.tsv:{entry.line}: the entry for `{entry.text}` "
        f"under '{entry.heading}' in docs/{entry.guide} (occurrence {entry.n}) "
        "matches no command of the guide: the command changed or went, so "
        "change or remove the entry"
        for entry in entries
        if entry.key not in present
    ]
    return out


def stage_commands(
    found: list[Command], entries: list[Entry], stage: str
) -> list[Command]:
    """The commands registered to `stage`, in guide order."""
    names = {e.key for e in entries if e.kind == "stage" and e.detail == stage}
    return [command for command in found if command.key in names]


def main(argv: list[str], registry: Path = REGISTRY, folder: Path = SOURCE) -> int:
    try:
        entries = parse_registry(registry.read_text())
        found = guide_commands(folder)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if argv[:1] == ["stage"] and len(argv) == 2:
        run = stage_commands(found, entries, argv[1])
        if not run:
            print(f"no command is registered to stage {argv[1]}", file=sys.stderr)
            return 1
        for command in run:
            sys.stdout.write(f"{command.guide}:{command.line}\0{command.text}\0")
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    errors = problems(found, entries)
    print("\n".join(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
