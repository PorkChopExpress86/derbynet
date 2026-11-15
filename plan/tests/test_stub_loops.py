"""Every loop in a stand-in script a test writes has a bound: a shell
`while` or `until` in a string of plan/tests or of a server test module.

A stand-in often runs in a session of its own, so a test run that is stopped
or killed leaves its loop running. #311's stubs looped on `while :`: three
were found 18 to 20 min old after a real run's hung tests, and a TaskStop of
a unittest run left one writing every 0.1 s (#311 retro).

Each loop, nested ones too, has a bound in its condition:
- `$SECONDS` in a script with a bash shebang, below a limit for `while`
  (`-lt`, `-le`) or at one for `until` (`-ge`, `-gt`); dash, /bin/sh here,
  has no `$SECONDS`, and its loop never ends;
- a counter compared the same way, which the body steps with `i=$((i + 1))`,
  such as
  `i=0; until [ -s f ] || [ "$i" -ge 300 ]; do i=$((i + 1)); sleep 0.1; done`;
- `$#` above 0, with a `shift` in the body;
- `read`, when no pipe feeds the loop (`yes | while read` never ends).
A `while` whose condition has `||`, or an `until` whose condition has `&&`,
has none, and neither has `for ((;;))`. A bound tested only in the body, such
as `[ "$SECONDS" -ge 5 ] && break`, counts as none: put it in the condition.
Strings joined with `+`, `%`, `.format` or `str.join` are read whole; one
built any other way, or a `done` inside a quoted word, is not. Docstrings are
left out.

Run: python3 -I -m unittest discover -s plan/tests
"""
import ast
import re
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

REPO = Path(__file__).resolve().parents[2]
BASH = re.compile(r'\A\s*#!\s*(?:/usr)?/bin/(?:env\s+)?bash\b')
HEAD = re.compile(r'\b(?P<kind>while|until)\b(?P<condition>[^\n]*?)(?:;|\n)\s*do\b')
C_FOR = re.compile(r'\bfor\s*\(\(\s*[^;)]*;\s*;')
KEYWORD = re.compile(r'\b(?:(?P<open>while|until|for|select)|(?P<do>do)|(?P<done>done))\b')
# The comparison that ends the loop: `while` runs below the limit, `until`
# stops at it.
ENDS = {'while': r'-(?:lt|le)', 'until': r'-(?:ge|gt)'}
JOINS = {'while': '||', 'until': '&&'}


def fold(node):
    """The text of a string node, or of strings joined with +, %, .format
    or str.join, with `{}` for each f-string field; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        value = node.value
        return value.decode(errors='replace') if isinstance(value, bytes) else value
    if isinstance(node, ast.JoinedStr):
        return ''.join(part.value if isinstance(part, ast.Constant) else '{}'
                       for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = fold(node.left), fold(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return fold(node.left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == 'format':
            return fold(node.func.value)
        if node.func.attr == 'join' and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
            parts = [fold(e) for e in node.args[0].elts]
            sep = fold(node.func.value)
            if sep is not None and None not in parts:
                return sep.join(parts)
    return None


def parts_of(node):
    """The nodes `fold` read into `node`'s text."""
    if isinstance(node, ast.JoinedStr):
        return node.values
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        args = node.args[:1]
        return [node.func.value, *args, *(e for a in args for e in getattr(a, 'elts', []))]
    return []


def strings(source):
    """The text of each string in `source` that is not a docstring, joined
    strings read whole, and its line."""
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
    texts = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        text = fold(node)
        if text is not None:
            skip.update(id(part) for part in parts_of(node))
            texts.append((node.lineno, text))
    return texts


def body_of(text, start):
    """The loop body that starts at `start`, just after its `do`, up to its
    own `done`."""
    depth = 1
    for word in KEYWORD.finditer(text, start):
        if word['open']:
            depth += 1
        elif word['done']:
            depth -= 1
            if depth == 0:
                return text[start:word.start()]
    return text[start:]


def bounded(kind, condition, body, bash, piped):
    if JOINS[kind] in condition:
        return False
    ends = ENDS[kind]
    if bash and re.search(r'\$\{?SECONDS\b\}?"?\s+' + ends + r'\s', condition):
        return True
    for counter in re.finditer(r'\$\{?(\w+)\}?"?\s+' + ends + r'\s+"?\d+', condition):
        name = counter[1]
        if name != 'SECONDS' and re.search(
                rf'\b{name}=\$\(\(\s*\$?{name}\s*\+\s*1\s*\)\)', body):
            return True
    if (kind == 'while' and re.search(r'\$#"?\s+-(?:gt|ge|ne)\s+0\b', condition)
            and re.search(r'\bshift\b', body)):
        return True
    return kind == 'while' and not piped and re.match(r'\s*(?:IFS=\S*\s+)?read\b', condition)


def unbounded(source):
    """Each shell loop in `source`'s strings with no bound, as (line, loop)."""
    found = []
    for line, text in strings(source):
        bash = bool(BASH.match(text))
        for head in HEAD.finditer(text):
            body = body_of(text, head.end())
            piped = bool(re.search(r'(?<!\|)\|\s*$', text[:head.start()]))
            if not bounded(head['kind'], head['condition'], body, bash, piped):
                found.append((line, ' '.join(f'{head[0]}{body}done'.split())))
        for loop in C_FOR.finditer(text):
            found.append((line, loop[0]))
    return found


def stand_in_modules():
    # This module's own examples are left out.
    yield from (p for p in sorted((REPO / 'plan' / 'tests').glob('*.py'))
                if p != Path(__file__).resolve())
    for path in sorted((REPO / 'server').rglob('*.py')):
        parts = path.relative_to(REPO / 'server').parts
        if parts[0] != '.venv' and 'tests' in parts[:-1]:
            yield path


class StubLoops(unittest.TestCase):
    def test_each_kind_of_loop(self):
        def found(text):
            return [loop for _, loop in unbounded(f'STUB = {text!r}\n')]

        bash = '#!/bin/bash\n'
        # #311's stubs, from bad55f07.
        self.assertEqual(found(bash + '  uv:hang)\n    while :; do date >>busy; sleep 0.1; done ;;\n'),
                         ['while :; do date >>busy; sleep 0.1; done'])
        for loop in ('while true; do sleep 1; done',
                     'until [ -s held ]; do sleep 0.1; done',
                     'while [ "$1" != "--output" ]; do shift; done',
                     'until [ "$(wc -l <f)" -ge 3 ]; do sleep 0.1; done',
                     'while sleep 1\ndo date\ndone',
                     # The #338 review's: dash has no $SECONDS, so this never ends.
                     '#!/bin/sh\nuntil [ -s f ] || [ "$SECONDS" -ge 1 ]; do sleep 0.2; done',
                     # A comparison, and $(( in the body, with no counter.
                     'until [ "$(wc -l <f)" -ge 3 ]; do x=$((1)); sleep 0.1; done',
                     bash + 'while [ "$SECONDS" -ge 0 ]; do sleep 0.1; done',
                     bash + 'while [ -e f ] || [ "$SECONDS" -lt 9 ]; do sleep 0.1; done',
                     # An unbounded loop inside a bounded one.
                     'while read -r x; do while :; do sleep 1; done; done',
                     'yes | while read -r x; do sleep 0.1; done',
                     'for ((;;)); do sleep 1; done',
                     'i=0; while [ "$i" -lt 9 ]; do sleep 1; done',
                     'i=0; until [ "$i" -ge 3 ]; do x=$((1)); sleep 0.1; done',
                     'while [ $# -gt 0 ]; do echo "$1"; done'):
            with self.subTest(loop=loop):
                self.assertEqual(len(found(loop)), 1, found(loop))
        for loop in (bash + 'while [ "$SECONDS" -lt "${STUB_LIMIT:-120}" ]; do sleep 0.1; done',
                     bash + 'until [ -s f ] || [ "$SECONDS" -ge 30 ]; do sleep 0.1; done',
                     'while [ $# -gt 0 ] && [ "$1" != "--output" ]; do shift; done',
                     'while read -r line; do echo "$line"; done <f',
                     'i=0; until [ -s f ] || [ "$i" -ge 300 ]; do i=$((i + 1)); sleep 0.1; done',
                     'for i in 1 2 3; do echo $i; done',
                     'the run waits while the checks run, and they do their work, then done'):
            with self.subTest(loop=loop):
                self.assertEqual(found(loop), [])
        # An f-string, strings joined by + or by str.join, and a docstring left out.
        self.assertEqual(len(unbounded('X = f"until [ -s {held} ]; do sleep 0.1; done"\n')), 1)
        self.assertEqual(len(unbounded('X = "while " + ":; do sleep 1; done"\n')), 1)
        self.assertEqual(len(unbounded('X = "\\n".join(["while :", "do sleep 1", "done"])\n')), 1)
        self.assertEqual(unbounded('"""Loops `while :; do sleep 1; done`."""\n'), [])

    def test_every_stand_in_loop_has_a_bound(self):
        found = [f'{path.relative_to(REPO)}:{line}: {loop}'
                 for path in stand_in_modules() for line, loop in unbounded(path.read_text())]
        self.assertEqual(found, [], 'bound each loop: see this module\'s docstring')


if __name__ == '__main__':
    unittest.main()
