"""Makes a plan/tests run refuse a `-k` pattern that matches no test.

unittest runs the tests that match any `-k`, so a misspelt pattern beside
one that matches drops its tests without a word: #311's `-k run_them_again`
matched nothing (the test says `runs_them_again`), and a broken test first
failed in pr-merge's real run (#311 retro). Every plan test module imports
this (test_k_patterns.py checks), so it covers `discover -s plan/tests`,
with or without `-p`, a pytest node id from server/, and a module run as a
script without `-I`. The import fails, and the run with it, where
plan/tests is not on `sys.path`: a script run under `-I`, and
`python3 -m unittest plan/tests/test_x.py` or `plan.tests.test_x` from the
repository's root.

The run stops before any test, with exit 2, unittest's usage error, naming
each pattern and the test names closest to it.

It also stops a run on a Python older than 3.14, with exit 2 and the fix.
The plan tools parse server/'s Python 3.14 (`except A, B:`), so under the
3.12 python3 of the lead's new machine three tests failed with a
SyntaxError that named no cause (#347 retro).
"""

import difflib
import fnmatch
import sys
import unittest
from pathlib import Path


def tests_of(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from tests_of(test)
        else:
            yield test


def every_name():
    """The name of each test method in the test modules loaded from this
    folder, the ones `-k` left out too."""
    here = Path(__file__).resolve().parent
    names = set()
    for module in list(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if not path or Path(path).resolve().parent != here:
            continue
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, unittest.TestCase):
                names.update(unittest.TestLoader().getTestCaseNames(value))
    return sorted(names)


def closest(word, names):
    """Up to three names that share the longest run of characters with
    `word`, at least two thirds of it."""
    def shared(name):
        return difflib.SequenceMatcher(None, word, name).find_longest_match().size

    ranked = sorted((n for n in names if 3 * shared(n) >= 2 * len(word)),
                    key=lambda n: (-shared(n), n))
    return ranked[:3]


def unmatched(patterns, suite):
    """Each pattern in `patterns` that names no test of `suite`, with the
    test names closest to it."""
    ids = [test.id() for test in tests_of(suite)]
    found = []
    for pattern in patterns or ():
        if not any(fnmatch.fnmatchcase(i, pattern) for i in ids):
            word = pattern.strip('*')
            found.append((word, closest(word, every_name())))
    return found


def refuse_unmatched(program):
    found = unmatched(program.testNamePatterns, program.test)
    for word, close in found:
        hint = f"; the closest names: {', '.join(close)}" if close else ''
        print(f'plan/tests: -k {word} matches no test{hint}', file=sys.stderr)
    if found:
        sys.exit(2)


def refuse_old_python():
    version = sys.version_info
    if tuple(version[:2]) < (3, 14):
        print(f"plan/tests: python3 is {'.'.join(map(str, version[:3]))}, and plan/tests "
              "need 3.14: put a 3.14 python3 first on PATH (plan/HANDOFF.md, Tools)",
              file=sys.stderr)
        sys.exit(2)


_run_tests = unittest.TestProgram.runTests
if not getattr(_run_tests, 'refuses_unmatched', False):

    def runTests(self, *args, **kwargs):
        refuse_old_python()
        refuse_unmatched(self)
        return _run_tests(self, *args, **kwargs)

    runTests.refuses_unmatched = True
    unittest.TestProgram.runTests = runTests
