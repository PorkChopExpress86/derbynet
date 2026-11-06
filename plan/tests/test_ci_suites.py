"""plan/bin/ci-suites against a fake gh, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI_SUITES = Path(__file__).resolve().parent.parent / 'bin' / 'ci-suites'


def run(number, body):
    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp) / 'gh'
        gh.write_text(f'#!{sys.executable}\nimport json\nprint(json.dumps({{"body": {body!r}}}))\n')
        gh.chmod(0o755)
        return subprocess.run([sys.executable, '-I', str(CI_SUITES), number],
                              env={**os.environ, 'GH': str(gh)},
                              capture_output=True, text=True)


class CiSuites(unittest.TestCase):
    def test_a_suite_the_workflow_lacks_fails(self):
        result = run('7', 'Runs testing/no-such-suite.sh.')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('MISSING  testing/no-such-suite.sh', result.stdout)

    def test_it_lists_the_parity_gate_entries_that_name_the_ticket(self):
        """#54 found late that its suite's parity-gate skip was its own
        question (#54 retro)."""
        result = run('54', 'Runs testing/test-balloting.sh.')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('in CI    testing/test-balloting.sh', result.stdout)
        self.assertIn('PARITY   test-balloting.sh: ', result.stdout)
        self.assertNotIn('#49', result.stdout)


if __name__ == '__main__':
    unittest.main()
