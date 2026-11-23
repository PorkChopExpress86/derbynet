"""k_patterns.py refuses a `-k` that matches no test, in a scratch copy of
plan/tests with one module, and every plan test module imports it.

Run: python3 -I -m unittest discover -s plan/tests
"""
import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

HERE = Path(__file__).resolve().parent
MODULE = '''import unittest

import k_patterns  # noqa: F401


class Pr(unittest.TestCase):
    def test_a_moved_master_runs_them_again(self):
        pass

    def test_a_pair_that_passed_is_not_run_again(self):
        pass


if __name__ == '__main__':
    unittest.main()
'''


class KPatterns(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        self.dir = Path(tmp)
        shutil.copy(HERE / 'k_patterns.py', self.dir)
        (self.dir / 'test_pr.py').write_text(MODULE)

    def run_them(self, *argv, script=False):
        if script:
            command = [sys.executable, str(self.dir / 'test_pr.py'), *argv]
        else:
            command = [sys.executable, '-I', '-m', 'unittest', 'discover', '-s', str(self.dir),
                       *argv]
        return subprocess.run(command, capture_output=True, text=True, timeout=60)

    def test_a_pattern_that_matches_nothing_beside_one_that_matches_is_refused(self):
        """#311's `-k run_them_again` matched nothing beside patterns that
        matched, and the run passed (#311 retro)."""
        for argv in (['-k', 'not_run_again', '-k', 'run_them_again'],
                     ['-p', 'test_pr.py', '-k', 'not_run_again', '-k', 'run_them_again']):
            with self.subTest(argv=argv):
                done = self.run_them(*argv)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn('plan/tests: -k run_them_again matches no test; the closest '
                              'names: ', done.stderr)
                self.assertIn('test_a_moved_master_runs_them_again', done.stderr)
                self.assertNotIn('Ran ', done.stderr)

    def test_a_module_run_as_a_script_refuses_it_too(self):
        done = self.run_them('-k', 'nothing_like_it', script=True)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('plan/tests: -k nothing_like_it matches no test\n', done.stderr)

    def test_patterns_that_each_match_run(self):
        for argv in ([], ['-k', 'runs_them_again', '-k', 'not_run_again'], ['-k', '*.Pr.test_a_*']):
            with self.subTest(argv=argv):
                done = self.run_them(*argv)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn('Ran 2 tests', done.stderr)
        done = self.run_them('-k', 'runs_them_again')
        self.assertIn('Ran 1 test', done.stderr)

    def test_a_python_older_than_3_14_is_refused_before_any_test(self):
        """The lead's new machine ran plan/tests under python3 3.12, and three
        failed with a SyntaxError that named no cause (#347 retro)."""
        for version, refused in (((3, 12, 3), True), ((3, 13, 9), True),
                                 ((3, 14, 0), False), ((3, 15, 2), False)):
            with self.subTest(version=version):
                done = subprocess.run(
                    [sys.executable, '-I', '-c',
                     'import runpy, sys\n'
                     f'sys.version_info = {version + ("final", 0)!r}\n'
                     f'sys.argv = ["python -m unittest", "discover", "-s", {str(self.dir)!r}]\n'
                     'runpy.run_module("unittest", run_name="__main__")\n'],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 2 if refused else 0, done.stderr)
                line = (f'plan/tests: python3 is {".".join(map(str, version))}, and plan/tests '
                        'need 3.14: put a 3.14 python3 first on PATH')
                self.assertEqual(line in done.stderr, refused, done.stderr)
                self.assertEqual('Ran 2 tests' in done.stderr, not refused, done.stderr)

    def test_every_plan_test_module_imports_it(self):
        missing = []
        # discover recurses into packages below plan/tests.
        for path in sorted(HERE.rglob('test*.py')):
            tree = ast.parse(path.read_text())
            names = {alias.name for node in tree.body if isinstance(node, ast.Import)
                     for alias in node.names}
            if 'k_patterns' not in names:
                missing.append(path.name)
        self.assertEqual(missing, [], 'add `import k_patterns  # noqa: F401` to each')


if __name__ == '__main__':
    unittest.main()
