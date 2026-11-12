"""Lists the numbers and quoted labels in a test file's docstrings and
comments, for the PR body's `<details>` block that names the assert reading
each one (plan/AGENT-BRIEF.md, Evidence).

#283's block was written from memory and missed about seven, among them
examples in the module docstring that no test asserted (#283 retro).

    uv run python tools/docstring_claims.py ui/tests/browser/test_x.py
    uv run python tools/docstring_claims.py --check BODY.md ui/tests/...

The first prints a Markdown table: where each claim is (the module, a
`#` comment above a constant, or the function), the claim, and the lines
of that scope's code where its literal appears, or `?` where none does.
A `?` is not a finding by itself: a share such as 15vw is asserted as
`0.15 * width`, which the tool tries, and a count may be a `len()`. Fill the
assert column by hand, or cut the claim.

`--check BODY.md` prints each claim that appears in none of the body's
`<details>` blocks and exits 1, or exits 0 when every claim is listed. It
matches the claim's text (`2.85vw`, `"Heat 100"`, `two lines`) as written.

A claim is a number with its unit (`58.3 px`, `2.85vw`, `1920x1080`, `22`),
a number word that counts something (`two lines`, `four tall windows`), or a
double-quoted string. Ticket and decision ids (`#273`, `D10`, `ADR-10`,
`C25`), file:line cites (`ondeck.css:49-51`) and numbers inside backticks
are left out: their own checks cover them (tests/test_php_cite_lines.py).
"""

from __future__ import annotations

import argparse
import ast
import io
import re
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


def listed_in(claim: str, listed: str) -> bool:
    """The claim as written, or a number word as its digits ("four
    landscape" as "4 landscape")."""
    if claim in listed:
        return True
    word, _, rest = claim.partition(" ")
    if word.lower() in WORDS[:12] and rest:
        return f"{WORDS.index(word.lower()) + 1} {rest}" in listed
    return False


def details(body: str) -> str:
    return "\n".join(re.findall(r"<details>(.*?)</details>", body, re.S))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", metavar="BODY.md", type=Path)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    found = [(f, c, hits) for f in args.files for c, hits in claims(f)]
    if args.check:
        listed = details(args.check.read_text())
        missing = [(f, c) for f, c, _ in found if not listed_in(c.text, listed)]
        for f, c in missing:
            print(f"{f}:{c.line}: {c.scope}: {c.text} is in no <details> block")
        return 1 if missing else 0
    print("| where | claim | code lines with its literal |")
    print("|---|---|---|")
    for f, c, hits in found:
        where = ", ".join(f"L{n}" for n in hits) or "?"
        print(f"| {f.name}:{c.line} `{c.scope}` | {c.text} | {where} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
