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


# A parity gate whose entries stay put, so these tests don't go stale when the
# real gate drops one (#218 removed test-balloting.sh's SKIPS entry).
FAKE_GATE = '''
_PAGES = (
    "the legacy pages: #40, #41"
)

WAITING = {
    "test-each-role.sh": Waiting(_PAGES),
    "test-basic-racing.sh": Waiting("#49 (history.php)", "history.php"),
    "test-each-role.sh (2)": Waiting(_PAGES),
}

SKIPS = {
    "test-balloting.sh": "#54 (D7): balloting moved to the backlog",
}

_SNAPSHOT = (
    "#221: the snapshot is compared as one text"
)

REPLIES_DIFFER = {
    "snapshot_get": _SNAPSHOT,
    "snapshot_put": _SNAPSHOT,
}
'''


def run(number, body):
    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp) / 'gh'
        gh.write_text(f'#!{sys.executable}\nimport json\nprint(json.dumps({{"body": {body!r}}}))\n')
        gh.chmod(0o755)
        gate = Path(tmp) / 'parity_gate.py'
        gate.write_text(FAKE_GATE)
        return subprocess.run([sys.executable, '-I', str(CI_SUITES), number],
                              env={**os.environ, 'GH': str(gh), 'PARITY_GATE': str(gate)},
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

    def test_it_reads_an_entry_that_is_a_constant(self):
        """REPLIES_DIFFER's entries name #221 through `_SNAPSHOT`."""
        result = run('221', 'Settles snapshot_get.')
        self.assertIn('PARITY   snapshot_get: ( "#221: the snapshot', result.stdout)
        self.assertIn('PARITY   snapshot_put: ', result.stdout)
        self.assertNotIn('test-balloting.sh', result.stdout)

    def test_it_reads_the_real_gate(self):
        """With no PARITY_GATE, the gate in server/tools is read."""
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp) / 'gh'
            gh.write_text(f'#!{sys.executable}\nprint(\'{{"body": ""}}\')\n')
            gh.chmod(0o755)
            result = subprocess.run([sys.executable, '-I', str(CI_SUITES), '1'],
                                    env={**os.environ, 'GH': str(gh)},
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_it_lists_the_parity_gate_entries_of_the_tickets_suites(self):
        """#204's WAITING entry named the legacy pages, not #204, so the
        listing left it out and the build found it late (#204 retro)."""
        result = run('204', 'Runs testing/test-each-role.sh.')
        self.assertIn('PARITY   test-each-role.sh: ', result.stdout)
        self.assertIn('PARITY   test-each-role.sh (2): ', result.stdout)
        self.assertNotIn('test-balloting.sh', result.stdout)


if __name__ == '__main__':
    unittest.main()
