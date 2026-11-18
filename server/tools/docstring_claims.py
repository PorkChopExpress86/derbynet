"""Lists the numbers and quoted labels in a test file's docstrings and
comments, for the PR body's `<details>` block that names the assert reading
each one (plan/AGENT-BRIEF.md, Evidence).

#283's block was written from memory and missed about seven, among them
examples in the module docstring that no test asserted (#283 retro).

    uv run python tools/docstring_claims.py ui/tests/browser/test_x.py
    uv run python tools/docstring_claims.py --check BODY.md ui/tests/...
    uv run python tools/docstring_claims.py --check BODY.md --base origin/master ...

The first prints a Markdown table: where each claim is (the module, a
`#` comment above a constant, or the function), the claim, and the lines
of that scope's code where its literal appears, or `?` where none does.
A `?` is not a finding by itself: a share such as 15vw is asserted as
`0.15 * width`, which the tool tries, and a count may be a `len()`. Fill the
assert column by hand, or cut the claim.

`--check BODY.md` prints each claim that no line of the body's `<details>`
blocks lists and exits 1, or exits 0 when every claim is listed. A line
lists a claim when it holds the claim's text as written (`2.85vw`,
`"Heat 100"`, `two lines`), as a whole token, and names its scope: the
test or class, or `module` or the file's name, as a row of the table does.
A `same` row or a wrapped line that names no scope lists for the scope of
the line above it; any other row that names none lists for none. A module
claim counts on any line, since its row may name the constant a comment
sits above, or the test that reads it.

A line of fenced code lists nothing and names no scope: #367's `<details>`
of pasted junit and traceback named the test and held `timed out after
3 s`, which passed a "3 s" no assert read (#367 retro). A fence opens on a
run of backticks with no backtick after it, or of tildes, behind any
indent, `>` marks and list markers, so the tool drops every line a
CommonMark fence holds, and more where it can't tell a list item's indent
(`test_the_fence_rule_drops_what_commonmark_fences` sweeps it). A fence
left open is an error that names its line.

`--check --base REV` asks only for the claims the diff from the merge base
with REV, to the working tree, adds or changes (D123, #374): those of a
scope (a top-level function or class) whose text differs, comments
included, or that is new, and those of each test `changed_tests()` in
`plan/bin/affected-tests` names, which adds the tests that read, directly
or through a helper, a fixture or a constant, a helper, fixture, constant
or import the diff adds, changes or deletes, as D101 counts them for its
repeats. Beyond D101's tests it names each top-level test with another
decorator than a mark, and each class that holds a test, however it is
named (a `unittest.TestCase`), or subclasses one, when it reads such a
change anywhere: a decorator, a base, a class attribute, a fixture method,
a mixin; and each subclass of a class it names (#379 review). A test
method's claims count as its class's, the scope this tool lists them
under. Every module claim counts when the module's docstring or
a module comment changes (D123), or any module-level statement other than
a def or class does, an import included (the lead's choice on #374, beyond
D123's words). Every claim of the file counts, as without `--base`, for a
file new since the merge base, or one `changed_tests()` can't resolve
(it can't be parsed, `global`, an assignment expression or a name bound
twice at module level), for a changed module-level statement that binds
nothing (`CASES.append(2)`), and for a change to or through a hook, an
xunit setup, `pytestmark` or an autouse fixture, which pytest applies to
every test (the lead's choice on #379), and the output says why. #356 owed 37 rows and
#360 owed 75 for tests they never changed (D123). The scoped check trusts
that the PR that last changed a test listed its claims.

A claim is a number with its unit (`58.3 px`, `2.85vw`, `1920x1080`, `22`),
a number word that counts something (`two lines`, `four tall windows`), a
double-quoted string, or a probe cited as a ticket's, a review's or a
log's (`#360's probe`, listed as `probe`): the row names the test that
holds what the probe showed. Ticket and decision
ids (`#273`, `D10`, `ADR-10`, `C25`), file:line cites (`ondeck.css:49-51`)
and numbers inside backticks are left out: their own checks cover them
(tests/test_php_cite_lines.py).
"""

from __future__ import annotations

import argparse
import ast
import functools
import importlib.machinery
import importlib.util
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

WORDS = (
    "one two three four five six seven eight nine ten eleven twelve twice half"
).split()
UNIT = r"(?:px|vw|vh|vmin|vmax|em|rem|%|ms|s|min|h)"
NUMBER = re.compile(
    rf"(?<![\w#.:-])\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?(?:\s?{UNIT}\b|(?=\W|$))"
)
WORD = re.compile(rf"\b(?:{'|'.join(WORDS)})(?:-|\s+)(?:[a-z]+)\b", re.IGNORECASE)
# Phrases that use a number word without counting anything: `# One read:`
# marks a one-shot read (ui/tests/test_browser_one_shot_reads.py).
IDIOM = re.compile(
    r"\bone\s+(?:read|task|that|of|by|at|another|way|place|out)\b", re.IGNORECASE
)
# A probe the prose cites as a ticket's, a review's or a log's ("#360's
# probe", "the #359 review's probe", "#341's PHP probe"): its finding is a
# claim like a number, listed beside the test that now asserts it. #348's
# helper docstring cited "the review's probe", which no test held, and
# cutting it cost a third head and its load runs (#348 retro). A probe
# named otherwise ("the probe's own shared lock", "after a probe that
# passed") is the code's own, not a cite (#370 review).
PROBE = re.compile(
    r"(?:#\d+(?:\s+review)?|\breview|\.log|\blog)['’]s\s+(?:[\w-]+\s+)?probe\b",
    re.IGNORECASE,
)
BACKTICKS = re.compile(r"`[^`\n]*`")
QUOTED = re.compile(r"\"([^\"]{1,120})\"|“([^”\n]{1,80})”")
# Spans whose numbers are ids or cites, not claims.
SKIP = re.compile(
    r"`[^`\n]*`"
    r"|#\d+"
    r"|\b(?:D|C|Q|ADR-?)\d+\b"
    r"|[\w./-]+\.(?:py|css|js|inc|php|md|html|kiosk|sh|txt)(?::\d+(?:-\d+)?)?"
    r"|\b(?:round|heat|lane)\s+\d+\b(?=,)"
)


@dataclass(frozen=True)
class Claim:
    scope: str
    line: int
    text: str


@dataclass
class Scope:
    name: str
    start: int
    end: int
    prose: list[tuple[int, str]]


def blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def claims_in(text: str) -> list[tuple[int, str]]:
    """The claims in a run of prose, with their offsets, in order. Quoted
    strings are taken first, so a number inside one is not listed twice."""
    found: list[tuple[int, str]] = []
    rest = text
    for m in QUOTED.finditer(text):
        found.append((m.start(), m.group(0)))
    rest = blank(rest, QUOTED)
    rest = blank(rest, SKIP)
    rest = blank(rest, IDIOM)
    for m in NUMBER.finditer(rest):
        found.append((m.start(), m.group(0).strip()))
    for m in WORD.finditer(rest):
        found.append((m.start(), m.group(0)))
    # On the text less its quotes and code spans: SKIP would blank the
    # ticket's id the cite needs.
    for m in PROBE.finditer(blank(blank(text, QUOTED), BACKTICKS)):
        found.append((m.end() - len("probe"), "probe"))
    return sorted(found)


def scopes(source: str) -> list[Scope]:
    """The module, each top-level function and class, and their prose:
    docstring lines and `#` comments. A comment outside every def belongs
    to the module, so the ones above a constant are listed under it."""
    tree = ast.parse(source)
    lines = source.count("\n") + 1
    module = Scope("module", 1, lines, [])
    found = [module]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            found.append(Scope(node.name, start, node.end_lineno or start, []))

    def owner(line: int) -> Scope:
        for s in found[1:]:
            if s.start <= line <= s.end:
                return s
        return module

    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            first = node.body[0].lineno
            target = module if isinstance(node, ast.Module) else owner(first)
            target.prose.append((first, doc))
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            owner(tok.start[0]).prose.append((tok.start[0], tok.string[1:]))
    for s in found:
        s.prose.sort()
    return found


def code_lines(source: str) -> set[int]:
    """Lines that hold code: not a comment-only line, not a docstring."""
    tree = ast.parse(source)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            if ast.get_docstring(node, clean=False) is not None:
                d = node.body[0]
                doc_lines.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    out = set()
    for n, text in enumerate(source.splitlines(), 1):
        if n in doc_lines or text.strip().startswith("#") or not text.strip():
            continue
        out.add(n)
    return out


def literals(claim: str) -> list[str]:
    """What the claim's value looks like in code."""
    if claim.startswith(('"', "“")):
        return [claim[1:-1]]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?\s?([a-z%]*)", claim)
    if not m:
        return []
    value, height, unit = m.groups()
    if height:
        return [f"{value}x{height}", f'"width": {value}, "height": {height}']
    out = [claim.replace(" ", ""), value]
    if unit in ("vw", "vh", "%"):
        out.append(f"{float(value) / 100:g}")
    return out


def find(claim: Claim, scope: Scope, source_lines: list[str], code: set[int]):
    hits = []
    for n in range(scope.start, scope.end + 1):
        if n not in code:
            continue
        text = source_lines[n - 1]
        for lit in literals(claim.text):
            if re.search(rf"(?<![\w.]){re.escape(lit)}(?![\w])", text):
                hits.append(n)
                break
    return hits


def claims(path: Path) -> list[tuple[Claim, list[int]]]:
    source = path.read_text()
    source_lines = source.splitlines()
    code = code_lines(source)
    out = []
    for scope in scopes(source):
        seen = set()
        for line, text in scope.prose:
            for at, c in claims_in(text):
                c = re.sub(r"\s+", " ", c)
                if c in seen:
                    continue
                seen.add(c)
                claim = Claim(scope.name, line + text.count("\n", 0, at), c)
                out.append((claim, find(claim, scope, source_lines, code)))
    return out


def methods(file: Path) -> dict[str, list[str]]:
    """Each top-level class's methods, by name."""
    return {
        node.name: [
            n.name
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.parse(file.read_text()).body
        if isinstance(node, ast.ClassDef)
    }


def scope_names(claim: Claim, file: Path) -> list[str]:
    """What a line names a claim's scope by: the test, or a class or one of
    its methods (#333's rows name the method), or for the module the word
    `module` or the file's name, as the table above does."""
    if claim.scope == "module":
        return ["module", file.name]
    return [claim.scope, *methods(file).get(claim.scope, [])]


# A table row, a list item or a heading starts an entry of its own; a row
# whose first cell is `same` (or a ditto mark) continues the one above.
NEW_ITEM = re.compile(r"\s*(?:\||[-*+]\s|\d+[.)]\s|#)")
SAME = re.compile(r"\s*\|\s*(?:same\b|〃|\")", re.IGNORECASE)
# A fence mark behind its indent, blockquote marks and list markers, then
# its info string.
FENCE = re.compile(r"((?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*)(`{3,}|~{3,})(.*)")


def word(text: str) -> re.Pattern:
    return re.compile(rf"(?<![\w.]){re.escape(text)}(?![\w])")


def rows(blocks: list[str], names: set[str]) -> list[tuple[set[str], str]]:
    """Each line of the `<details>` blocks with the scopes it lists claims
    for: those it names, or, naming none, those of the line above it when
    it is a `same` row or a wrapped line. Any other row or list item that
    names no scope lists none: a test named by nickname is #324's failure
    (#340 review)."""
    found = {n: word(n) for n in names}
    out = []
    for block in blocks:
        current: set[str] = set()
        for line in block.splitlines():
            if named := {n for n, pattern in found.items() if pattern.search(line)}:
                current = named
            elif NEW_ITEM.match(line) and not SAME.match(line):
                current = set()
            out.append((current, line))
    return out


def listed_in(claim: Claim, file: Path, listed: list[tuple[set[str], str]]) -> bool:
    """The claim as written, or a number word as its digits ("four
    landscape" as "4 landscape"), as a whole token on a line that lists its
    scope's claims (`rows()`). #324's 2 passed for a 2 in another test's
    row (#324 retro). A module claim counts on any line."""
    forms = [claim.text]
    first, _, rest = claim.text.partition(" ")
    if first.lower() in WORDS[:12] and rest:
        forms.append(f"{WORDS.index(first.lower()) + 1} {rest}")
    pattern = re.compile("|".join(word(f).pattern for f in forms))
    if claim.scope == "module":
        # A module claim's row may name the constant a comment sits above,
        # and a test that reads it (#333's `STUB` comment row).
        return any(pattern.search(line) for _, line in listed)
    names = set(scope_names(claim, file))
    return any(scopes & names and pattern.search(line) for scopes, line in listed)


AFFECTED_TESTS = Path(__file__).resolve().parents[2] / "plan" / "bin" / "affected-tests"


@functools.cache
def affected_tests():
    """plan/bin/affected-tests as a module, for its `changed_tests()`."""
    loader = importlib.machinery.SourceFileLoader("affected_tests", str(AFFECTED_TESTS))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def frame(source: str) -> list[str]:
    """The module-level statements less the top-level defs and classes,
    which are scopes of their own, by AST."""
    return [
        ast.dump(n)
        for n in ast.parse(source).body
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def texts(source: str) -> dict[str, list[list[str]]]:
    """Each top-level scope's lines, decorators included, by its name."""
    lines = source.splitlines()
    out: dict[str, list[list[str]]] = {}
    for s in scopes(source)[1:]:
        out.setdefault(s.name, []).append(lines[s.start - 1 : s.end])
    return out


def counted(base: str | None, head: str) -> tuple[set[str] | None, str]:
    """The scopes whose claims `--check --base` asks for, from `base`, the
    file's text at the merge base (None when it is new), to `head`: a set
    of scope names, or None and why when every claim counts."""
    if base is None:
        return None, "new since the merge base"
    tool = affected_tests()
    try:
        picked = tool.changed_tests(base, head, lambda name: None, claims=True)
    except tool.WholeFile as why:
        return None, str(why)
    old, new = texts(base), texts(head)
    names = {name for name, text in new.items() if old.get(name) != text}
    names |= {key.split("::")[0] for key, _ in picked}
    prose = [[text for _, text in scopes(s)[0].prose] for s in (base, head)]
    if prose[0] != prose[1] or frame(base) != frame(head):
        names.add("module")
    return names, ""


def base_text(file: Path, rev: str) -> str | None:
    """`file` as it was at the merge base of `rev` and HEAD, or None when
    it is not there."""

    def git(*argv: str) -> str:
        return subprocess.run(
            ["git", *argv], cwd=file.parent, check=True, capture_output=True, text=True
        ).stdout

    base = git("merge-base", rev, "HEAD").strip()
    try:
        return git("show", f"{base}:./{file.name}")
    except subprocess.CalledProcessError:
        return None


class UnclosedFence(Exception):
    def __init__(self, line: int):
        super().__init__(line)
        self.line = line


def opens(mark: re.Match) -> bool:
    """A backtick fence's info string holds no backtick, or the line is an
    inline code span (```code``` is a span)."""
    return mark[2][0] == "~" or "`" not in mark[3]


def closes(mark: re.Match, fence: re.Match) -> bool:
    """The fence's own mark, at least as long, with nothing after it, and
    indented within the room one container gives both lines' indent."""
    return (
        mark[2][0] == fence[2][0]
        and len(mark[2]) >= len(fence[2])
        and not mark[3].strip()
        and abs(width(mark[1]) - width(fence[1])) <= 3
    )


def width(prefix: str) -> int:
    return len(prefix.expandtabs(4))


def details(body: str) -> list[str]:
    """The text of each `<details>` block, less its fenced code: pasted
    output lists no claim (#367 retro), and a `<details>` a fence quotes
    opens no block. Raises UnclosedFence for a fence left open, which
    would drop the rest of the body."""
    kept, fence, start = [], None, 0
    for n, line in enumerate(body.splitlines(), 1):
        mark = FENCE.match(line)
        if fence is None and mark and opens(mark):
            fence, start = mark, n
        elif fence is None:
            kept.append(line)
        elif mark and closes(mark, fence):
            fence = None
    if fence is not None:
        raise UnclosedFence(start)
    return re.findall(r"<details>(.*?)</details>", "\n".join(kept), re.S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", metavar="BODY.md", type=Path)
    parser.add_argument("--base", metavar="REV", help="with --check: see above")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.base and not args.check:
        parser.error("--base goes with --check")
    found = [(f, c, hits) for f in args.files for c, hits in claims(f)]
    if args.check:
        names = {n for f, c, _ in found for n in scope_names(c, f)}
        try:
            blocks = details(args.check.read_text())
        except UnclosedFence as e:
            print(
                f"{args.check}:{e.line}: this fence never closes, so the rest"
                " of the body, every <details> block in it, reads as code"
            )
            return 1
        listed = rows(blocks, names)
        scoped = {}
        if args.base:
            for f in dict.fromkeys(args.files):
                names, why = counted(base_text(f, args.base), f.read_text())
                scoped[f] = names
                if names is None:
                    print(f"{f}: every claim counts (D123): {why}")
        missing = [
            (f, c)
            for f, c, _ in found
            if (scoped.get(f) is None or c.scope in scoped[f])
            and not listed_in(c, f, listed)
        ]
        for f, c in missing:
            where = (
                ""
                if c.scope == "module"
                else f" line that names {c.scope}"
                " (by its function name, not a nickname)"
            )
            print(f"{f}:{c.line}: {c.scope}: {c.text} is in no <details> block{where}")
        return 1 if missing else 0
    print("| where | claim | code lines with its literal |")
    print("|---|---|---|")
    for f, c, hits in found:
        where = ", ".join(f"L{n}" for n in hits) or "?"
        print(f"| {f.name}:{c.line} `{c.scope}` | {c.text} | {where} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
