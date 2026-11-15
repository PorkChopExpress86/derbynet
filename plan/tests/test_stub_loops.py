"""Every loop in a stand-in script a test writes has a bound: a shell
`while` or `until` in a string of plan/tests or of a server test module.

A stand-in often runs in a session of its own, so a test run that is stopped
or killed leaves its loop running. #311's stubs looped on `while :`: three
were found 18 to 20 min old after a real run's hung tests, and a TaskStop of
a unittest run left one writing every 0.1 s (#311 retro).

A loop has a bound when its condition reads `$SECONDS`, counts the
arguments (`$#`), reads its input (`while read`), or compares a counter the
body steps with `$((...))`, such as
`i=0; until [ -s f ] || [ "$i" -ge 300 ]; do i=$((i + 1)); sleep 0.1; done`
for dash, which has no `$SECONDS`. Docstrings are left out.

Run: python3 -I -m unittest discover -s plan/tests
"""
import ast
import re
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

REPO = Path(__file__).resolve().parents[2]
LOOP = re.compile(r'\b(while|until)\b(?P<condition>[^\n]*?)(?:;\s*|\n\s*)do\b(?P<body>.*?)\bdone\b',
                  re.S)
BOUNDED = re.compile(r'\$\{?SECONDS\b|\$#|^\s*read\b')
COUNTER = re.compile(r'-(?:lt|le|gt|ge)\b')


def strings(source):
    """The text of each string in `source` that is not a docstring, with
    `{}` for each f-string field, and its line."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            if id(node) not in docstrings:
                value = node.value
                yield node.lineno, value.decode(errors='replace') if isinstance(value, bytes) else value
        elif isinstance(node, ast.JoinedStr):
            yield node.lineno, ''.join(
                part.value if isinstance(part, ast.Constant) else '{}' for part in node.values)


def unbounded(source):
    """Each shell loop in `source`'s strings with no bound, as (line, loop)."""
    found = []
    for line, text in strings(source):
        for loop in LOOP.finditer(text):
            condition, body = loop['condition'], loop['body']
            if BOUNDED.search(condition):
                continue
            if COUNTER.search(condition) and '$((' in body:
                continue
            found.append((line, ' '.join(loop[0].split())))
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

        # #311's stubs, from bad55f07.
        self.assertEqual(found('  uv:hang)\n    while :; do date >>busy; sleep 0.1; done ;;\n'),
                         ['while :; do date >>busy; sleep 0.1; done'])
        for loop in ('while true; do sleep 1; done',
                     'until [ -s held ]; do sleep 0.1; done',
                     'while [ "$1" != "--output" ]; do shift; done',
                     'until [ "$(wc -l <f)" -ge 3 ]; do sleep 0.1; done',
                     'while sleep 1\ndo date\ndone'):
            with self.subTest(loop=loop):
                self.assertEqual(len(found(loop)), 1)
        for loop in ('while [ "$SECONDS" -lt "${STUB_LIMIT:-120}" ]; do sleep 0.1; done',
                     'while [ $# -gt 0 ] && [ "$1" != "--output" ]; do shift; done',
                     'while read -r line; do echo "$line"; done',
                     'i=0; until [ -s f ] || [ "$i" -ge 300 ]; do i=$((i + 1)); sleep 0.1; done',
                     'for i in 1 2 3; do echo $i; done',
                     'the run waits while the checks run, and they do their work, then done'):
            with self.subTest(loop=loop):
                self.assertEqual(found(loop), [])
        # An f-string, and a docstring left out.
        self.assertEqual(len(unbounded('X = f"until [ -s {held} ]; do sleep 0.1; done"\n')), 1)
        self.assertEqual(unbounded('"""Loops `while :; do sleep 1; done`."""\n'), [])

    def test_every_stand_in_loop_has_a_bound(self):
        found = [f'{path.relative_to(REPO)}:{line}: {loop}'
                 for path in stand_in_modules() for line, loop in unbounded(path.read_text())]
        self.assertEqual(found, [], 'bound each loop: see this module\'s docstring')


if __name__ == '__main__':
    unittest.main()
