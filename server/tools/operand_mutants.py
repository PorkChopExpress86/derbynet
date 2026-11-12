"""Lists the operand mutations of each `min()`, `max()` and `clamp()` and
each condition a diff adds, for the mutation table (plan/AGENT-BRIEF.md:
"mutate each operand").

#283's caps were `min(1em, 2.85vw)` and `min(1em, 2.5vw)`. Its mutations
removed whole caps or resized them, and none kept `2.85vw` alone, so the
`1em` operand went untested until review (#283 retro). This lists those
mutants; running them, and the table, stay with the implementer.

    uv run python tools/operand_mutants.py                  # diff against origin/master
    uv run python tools/operand_mutants.py --base REV -- ui/static/ui/kiosk.css
    git diff A B | uv run python tools/operand_mutants.py --stdin

Each output line is `path:line  kind  old  ->  new`, where `old` occurs on
that line of the new file. For each added line it lists:

- `min(a, b, ...)` and `max(...)` (CSS, `Math.min`, Python): the call
  replaced by each operand alone;
- `clamp(lo, x, hi)`: `x` alone, `max(lo, x)` (no upper bound) and
  `min(x, hi)` (no lower bound);
- a nested call's own mutants too, so `clamp(1rem, min(9vh, 11vw), 8rem)`
  lists `min(9vh, 11vw)`'s;
- a condition, JavaScript's `if (...)` and `.filter(x => ...)` and
  Python's `if ...:`, `elif ...:` and a one-line comprehension's `if`, made
  true and made false, and, when
  it splits at the top level into `&&`/`||` (or `and`/`or`) operands, each
  operand made true and made false.

It reads one line at a time, so a call or a condition that spans lines,
a ternary, and a condition in a `while`, a `return` or an arrow function
outside `.filter()` are left out: list those by hand.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CALL = re.compile(r"(?<![\w-])(min|max|clamp)\(")
JS_IF = re.compile(r"\b(?:if|else if)\s*\(")
FILTER = re.compile(r"\.filter\(\s*(\(?[\w, ]*\)?)\s*=>\s*")
PY_IF = re.compile(r"^\s*(?:if|elif)\s+(.+):\s*(?:#.*)?$")
OPEN, CLOSE = "([{", ")]}"


@dataclass(frozen=True)
class Mutant:
    path: str
    line: int
    kind: str
    old: str
    new: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.kind}  {self.old}  ->  {self.new}"


def closing(text: str, start: int) -> int | None:
    """The index of the bracket that closes the one opened just before
    `start`, skipping strings; None when the line ends first."""
    depth, quote = 1, None
    i = start
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in OPEN:
            depth += 1
        elif c in CLOSE:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def split_top(text: str, seps: list[str]) -> list[str]:
    """`text` split at each separator outside brackets and strings."""
    parts, depth, quote, last, i = [], 0, None, 0, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in OPEN:
            depth += 1
        elif c in CLOSE:
            depth -= 1
        elif depth == 0:
            for sep in seps:
                if text.startswith(sep, i):
                    parts.append(text[last:i].strip())
                    last = i + len(sep)
                    i = last - 1
                    break
        i += 1
    parts.append(text[last:].strip())
    return parts


def call_mutants(text: str) -> list[tuple[str, str, str]]:
    out = []
    for m in CALL.finditer(text):
        end = closing(text, m.end())
        if end is None:
            continue
        name, inner = m.group(1), text[m.end() : end]
        call = text[m.start() : end + 1]
        args = split_top(inner, [","])
        if name == "clamp" and len(args) == 3:
            lo, x, hi = args
            out.append(("clamp", call, x))
            out.append(("clamp", call, f"max({lo}, {x})"))
            out.append(("clamp", call, f"min({x}, {hi})"))
        elif name in ("min", "max") and len(args) > 1:
            for a in args:
                out.append((name, call, a))
    return out


def condition_mutants(cond: str, js: bool) -> list[tuple[str, str, str]]:
    true, false = ("true", "false") if js else ("True", "False")
    seps = [" && ", " || "] if js else [" and ", " or "]
    out = [("condition", cond, true), ("condition", cond, false)]
    operands = split_top(cond, seps)
    if len(operands) > 1:
        for op in operands:
            out.append(("operand", op, true))
            out.append(("operand", op, false))
    return out


def comprehension_mutants(text: str) -> list[tuple[str, str, str]]:
    """A one-line comprehension's filter, `[x for x in xs if COND]`."""
    out = []
    for m in re.finditer(r"\sfor\s.+?\sin\s.+?\sif\s+", text):
        depth, start = 0, None
        for i in range(m.start(), -1, -1):
            if text[i] in CLOSE:
                depth += 1
            elif text[i] in OPEN:
                if depth == 0:
                    start = i
                    break
                depth -= 1
        if start is None:
            continue
        end = closing(text, start + 1)
        if end is None or end < m.end():
            continue
        out += condition_mutants(text[m.end() : end].strip(), js=False)
    return out


def line_mutants(path: str, text: str) -> list[tuple[str, str, str]]:
    found = call_mutants(text)
    if path.endswith(".py"):
        m = PY_IF.match(text)
        if m:
            found += condition_mutants(m.group(1).strip(), js=False)
        found += comprehension_mutants(text)
        # JavaScript inside a Python string, as the browser tests keep it.
    if not path.endswith(".css"):
        for m in JS_IF.finditer(text):
            end = closing(text, m.end())
            if end is not None:
                found += condition_mutants(text[m.end() : end].strip(), js=True)
        for m in FILTER.finditer(text):
            end = closing(text, text.index("(", m.start()) + 1)
            if end is None:
                continue
            param, body = m.group(1).strip(), text[m.end() : end].strip()
            whole = text[m.start(1) : end].strip()
            found.append(("filter", whole, f"{param} => true"))
            found.append(("filter", whole, f"{param} => false"))
            found += condition_mutants(body, js=True)[2:]
    return found


def added_lines(diff: str):
    """(path, new line number, text) for each line the diff adds."""
    path, line = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif path and raw.startswith("+"):
            yield path, line, raw[1:]
            line += 1
        elif path and not raw.startswith("-") and not raw.startswith("\\"):
            line += 1


def css_code(root: Path | None, path: str) -> list[str] | None:
    """A stylesheet's lines with its comments blanked, so the values a
    comment quotes are not listed; None when the file is not on disk."""
    if root is None or not path.endswith(".css"):
        return None
    try:
        text = (root / path).read_text()
    except OSError:
        return None

    def spaces(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    blanked = re.sub(r"/\*.*?\*/", spaces, text, flags=re.S)
    return blanked.splitlines()


def mutants(diff: str, root: Path | None = None) -> list[Mutant]:
    out = []
    files: dict[str, list[str] | None] = {}
    for path, line, text in added_lines(diff):
        if path not in files:
            files[path] = css_code(root, path)
        code = files[path]
        if code is not None and line <= len(code):
            text = code[line - 1]
        for kind, old, new in line_mutants(path, text):
            if old != new:
                out.append(Mutant(path, line, kind, old, new))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--stdin", action="store_true", help="read the diff from stdin")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    root = Path(top.stdout.strip()) if top.returncode == 0 else None
    if args.stdin:
        diff = sys.stdin.read()
    else:
        diff = subprocess.run(
            ["git", "diff", "-U0", args.base, "--", *args.paths],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    found = mutants(diff, root)
    for m in found:
        print(m)
    if not found:
        print("no min(), max(), clamp() or condition on an added line", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
