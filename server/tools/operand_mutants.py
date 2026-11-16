"""Lists the operand mutations of each `min()`, `max()` and `clamp()` and
each condition a diff adds, for the mutation table (plan/AGENT-BRIEF.md:
"mutate each operand").

#283's caps were `min(1em, 2.85vw)` and `min(1em, 2.5vw)`. Its mutations
removed whole caps or resized them, and none kept `2.85vw` alone, so the
`1em` operand went untested until review (#283 retro). This lists those
mutants, and `tools/mutate.py --listed` runs every one (#336 retro).

    uv run python tools/operand_mutants.py      # from the merge base with origin/master
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
- a condition, JavaScript's `if (...)` and `.filter(x => ...)` and, when
  the file can't be read or parsed, Python's `if ...:`, `elif ...:` and a
  one-line comprehension's `if`, made true and made false (Python: a `.py`
  file, or a script with no extension whose shebang names Python, such as
  `plan/bin/affected-tests`), and, when
  it splits at the top level into `&&`/`||` (or `and`/`or`) operands, each
  operand made true and made false.

A Python file on disk is read by its AST instead, for its conditions:
each `if`, `elif`, `while`, ternary, comprehension and `case` guard
condition, and each
operand of every `and` and `or` (a `return a and b` too), wherever it
spans lines, when it touches an added line. Its `old` is then the text of
its span, which can hold a newline, printed on one line; the line printed
is the span's first. A loop whose header touches an added line gets a
mutant whose body never runs: a `while` its condition made False, a `for`
its iterable made `()` (#345's scanner loop survived `break` in review).
The rest is read one line at a time, so a JavaScript call or condition that
spans lines, a ternary, and a condition in a `while`, a `return` or an
arrow function outside `.filter()` are left out, and so is every branch
that is no condition in either language (a tuple element such as
`(ast.Global, ast.Nonlocal)`, a loop's bound): list those by hand.

A Python file read by its AST also lists each member of a set literal
(`frozenset({...})`'s too) and of the tuple or list an `in` or `not in`
tests, taken away with its comma (#356's review took `"range"` out of a
lookup set, and the mutant survived).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
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
    # Where `old` sits in the file, as (start, end) offsets into its text;
    # None when the file was not read, the line is past its end, or `old` is
    # on its line more than once.
    span: tuple[int, int] | None = field(default=None, compare=False)

    def __str__(self) -> str:
        old = " ".join(self.old.split())
        return f"{self.path}:{self.line}  {self.kind}  {old}  ->  {self.new}"


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


def is_python(root: Path | None, path: str) -> bool:
    """A `.py` file, or a script whose shebang names Python, such as
    `plan/bin/affected-tests` (#320 retro)."""
    if path.endswith(".py"):
        return True
    if root is None or Path(path).suffix:
        return False
    try:
        with open(root / path, "rb") as f:
            first = f.readline()
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def line_mutants(
    path: str, text: str, python: bool, by_ast: bool = False
) -> list[tuple[str, str, str]]:
    """The mutants one line holds; with `by_ast`, its Python conditions are
    left to python_conditions()."""
    found = call_mutants(text)
    py_if = None
    if python and (m := PY_IF.match(text)):
        if not by_ast:
            found += condition_mutants(m.group(1).strip(), js=False)
        py_if = len(text) - len(text.lstrip())
    if python and not by_ast:
        found += comprehension_mutants(text)
        # JavaScript inside a Python string, as the browser tests keep it; a
        # Python `if (a, b) in seen:` is no JavaScript `if (...)`.
    if not path.endswith(".css"):
        for m in JS_IF.finditer(text):
            if m.start() == py_if:
                continue
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


def offsets(text: str) -> list[int]:
    """The offset of each line's start in `text`, for 1-based line numbers."""
    starts = [0, 0]
    for i, c in enumerate(text):
        if c == "\n":
            starts.append(i + 1)
    return starts


# More members than this in one collection make a table, not a rule: its
# members get no rows, and main() names it (#370 review: a 400-name set
# listed 400 mutants).
MAX_MEMBERS = 20


def crowded(text: str, added: set[int]) -> list[tuple[int, int]]:
    """(line, members) of each set literal, or tuple or list an `in` tests,
    that touches a line in `added` and has more than MAX_MEMBERS members."""
    tree = ast.parse(text)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Set):
            found[id(node)] = node
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(
                    right, (ast.Tuple, ast.List, ast.Set)
                ):
                    found[id(right)] = right
    return sorted(
        (node.lineno, len(node.elts))
        for node in found.values()
        if len(node.elts) > MAX_MEMBERS
        and any(n in added for n in range(node.lineno, node.end_lineno + 1))
    )


def python_conditions(
    text: str, added: set[int]
) -> list[tuple[int, str, str, str, tuple[int, int]]]:
    """(line, kind, old, new, span) for each condition and `and`/`or` operand
    of a Python module that touches a line in `added`; [] when it does not
    parse, which mutants() takes as no AST."""
    tree = ast.parse(text)
    lines = text.split("\n")
    starts = offsets(text)

    def at(line: int, col: int) -> int:
        # ast's columns count UTF-8 bytes.
        return starts[line] + len(lines[line - 1].encode()[:col].decode())

    def entry(node: ast.expr, kind: str, new: str):
        span = (
            at(node.lineno, node.col_offset),
            at(node.end_lineno, node.end_col_offset),
        )
        return (node.lineno, kind, text[span[0] : span[1]], new, span)

    def touches(node: ast.expr) -> bool:
        return any(n in added for n in range(node.lineno, node.end_lineno + 1))

    tests, ops, loops, members = [], [], [], {}
    for node in ast.walk(tree):
        # A set literal, `frozenset({...})`'s too, and the tuple or list an
        # `in` tests: #356's review took `"range"` out of LOOKUPS and
        # `"values"` out of `("values_list", "values")`, and both survived.
        if isinstance(node, ast.Set):
            members[id(node)] = node
        elif isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(
                    right, (ast.Tuple, ast.List, ast.Set)
                ):
                    members[id(right)] = right
        if isinstance(node, (ast.For, ast.AsyncFor)):
            loops.append(node)
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            tests.append(node.test)
        elif isinstance(node, ast.comprehension):
            tests += node.ifs
        elif isinstance(node, ast.match_case) and node.guard is not None:
            tests.append(node.guard)
        elif isinstance(node, ast.BoolOp):
            ops += node.values
    out = []
    for node in tests:
        if touches(node):
            out += [entry(node, "condition", "True"), entry(node, "condition", "False")]
    for node in ops:
        if touches(node):
            out += [entry(node, "operand", "True"), entry(node, "operand", "False")]
    # A loop's body that never runs (#345 retro); a `while` has it above.
    for node in loops:
        header = range(node.lineno, node.iter.end_lineno + 1)
        if any(n in added for n in header):
            out.append(entry(node.iter, "loop", "()"))
    # Each member taken away, with the comma beside it: the one after it,
    # or for the last, the one before it. A lone member's span is empty,
    # so mutants() drops it (old == new). A starred member is left whole:
    # `(*a, b)` less `b` no longer parses. A tuple of two keeps its comma,
    # since `("values_list")` is a string, and `in` on it a substring test
    # that `"values"` passes (#370 review). A collection of more than
    # MAX_MEMBERS is a table, not a rule: crowded() names it instead.
    for node in members.values():
        elts = node.elts
        if any(isinstance(e, ast.Starred) for e in elts) or len(elts) > MAX_MEMBERS:
            continue
        pair = (
            isinstance(node, ast.Tuple)
            and len(elts) == 2
            and not text[at(elts[1].end_lineno, elts[1].end_col_offset) :]
            .lstrip()
            .startswith(",")
        )
        for i, elt in enumerate(elts):
            if not touches(elt):
                continue
            if pair:
                span = (
                    at(elts[0].lineno, elts[0].col_offset),
                    at(elts[1].end_lineno, elts[1].end_col_offset),
                )
                other = elts[1 - i]
                kept = text[
                    at(other.lineno, other.col_offset) : at(
                        other.end_lineno, other.end_col_offset
                    )
                ]
                out.append(
                    (elt.lineno, "member", text[span[0] : span[1]], kept + ",", span)
                )
                continue
            if i + 1 < len(elts):
                span = (
                    at(elt.lineno, elt.col_offset),
                    at(elts[i + 1].lineno, elts[i + 1].col_offset),
                )
            else:
                before = elts[i - 1]
                span = (
                    at(before.end_lineno, before.end_col_offset),
                    at(elt.end_lineno, elt.end_col_offset),
                )
            out.append((elt.lineno, "member", text[span[0] : span[1]], "", span))
    return sorted(out, key=lambda e: (e[4][0], e[1] != "condition", -e[4][1]))


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


def read(path: Path) -> str:
    """A file's text as `tools/mutate.py` reads it, bytes decoded as UTF-8
    with no newline translation, so a CRLF file's spans are its own (#358
    review: `read_text()` made them LF, and every mutant was refused)."""
    return path.read_bytes().decode()


def css_code(root: Path | None, path: str) -> list[str] | None:
    """A stylesheet's lines with its comments blanked, so the values a
    comment quotes are not listed; None when the file is not on disk."""
    if root is None or not path.endswith(".css"):
        return None
    try:
        text = read(root / path)
    except OSError, UnicodeDecodeError:
        return None

    def spaces(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    blanked = re.sub(r"/\*.*?\*/", spaces, text, flags=re.S)
    return blanked.splitlines()


def source(root: Path | None, path: str) -> str | None:
    if root is None:
        return None
    try:
        return read(root / path)
    except OSError, UnicodeDecodeError:
        return None


def mutants(diff: str, root: Path | None = None) -> list[Mutant]:
    """Each mutant the diff's added lines hold, by file and then by line;
    with `root`, the tree the diff's new side is checked out in, a Python
    file's conditions come from its AST, and each mutant has its span."""
    added: dict[str, list[tuple[int, str]]] = {}
    for path, line, text in added_lines(diff):
        added.setdefault(path, []).append((line, text))
    out = []
    for path, rows in added.items():
        code, python, text = (
            css_code(root, path),
            is_python(root, path),
            source(root, path),
        )
        conditions = []
        if python and text is not None:
            try:
                conditions = python_conditions(text, {line for line, _ in rows})
                by_ast = True
            except SyntaxError:
                by_ast = False
        else:
            by_ast = False
        starts = offsets(text) if text is not None else None
        lines = text.split("\n") if text is not None else None
        found = []
        for line, row in rows:
            if code is not None and line <= len(code):
                row = code[line - 1]
            for kind, old, new in line_mutants(path, row, python, by_ast):
                span = None
                if (
                    lines is not None
                    and line <= len(lines)
                    and lines[line - 1].count(old) == 1
                ):
                    start = starts[line] + lines[line - 1].index(old)
                    span = (start, start + len(old))
                found.append((line, 0, Mutant(path, line, kind, old, new, span)))
        for line, kind, old, new, span in conditions:
            found.append((line, 1, Mutant(path, line, kind, old, new, span)))
        found.sort(key=lambda f: (f[0], f[1]))
        out += [m for _, _, m in found if m.old != m.new]
    return out


def crowded_in(diff: str, root: Path | None) -> list[tuple[str, int, int]]:
    """(path, line, members) of each crowded() collection in the diff's
    Python files on disk."""
    added: dict[str, set[int]] = {}
    for path, line, _ in added_lines(diff):
        added.setdefault(path, set()).add(line)
    out = []
    for path, lines in added.items():
        text = source(root, path)
        if text is None or not is_python(root, path):
            continue
        try:
            out += [(path, line, n) for line, n in crowded(text, lines)]
        except SyntaxError:
            continue
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
            # From the merge base, as affected-tests diffs: from BASE itself,
            # a line master changed after the branch left it read as added
            # (#348 retro).
            ["git", "diff", "-U0", "--merge-base", args.base, "--", *args.paths],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    found = mutants(diff, root)
    for m in found:
        print(m)
    for path, line, members in crowded_in(diff, root):
        print(
            f"{path}:{line}: {members} members, over {MAX_MEMBERS}: no member rows;"
            " take away by hand the ones a rule reads",
            file=sys.stderr,
        )
    if not found:
        print("no min(), max(), clamp() or condition on an added line", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
