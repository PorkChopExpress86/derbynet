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

    def test_every_plan_test_module_imports_it(self):
        missing = []
        for path in sorted(HERE.glob('test*.py')):
            tree = ast.parse(path.read_text())
            names = {alias.name for node in tree.body if isinstance(node, ast.Import)
                     for alias in node.names}
            if 'k_patterns' not in names:
                missing.append(path.name)
        self.assertEqual(missing, [], 'add `import k_patterns  # noqa: F401` to each')


if __name__ == '__main__':
    unittest.main()
