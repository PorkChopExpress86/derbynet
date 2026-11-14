"""plan/bin/pr-wait against a fake gh that answers `pr checks` with canned
JSON run through jq with pr-wait's own --jq filter, so the filter is tested.

Run: python3 -I -m unittest discover -s plan/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PR_WAIT = Path(__file__).resolve().parent.parent / 'bin' / 'pr-wait'

FAKE_GH = '''#!{python}
import subprocess, sys
args = sys.argv[1:]
if args[2:3] != [{pr!r}]:
    sys.exit('pr-wait named ' + ' '.join(args[2:3]))
if args[:2] == ['pr', 'view'] and 'headRefOid' in args:
    print('abc123')
elif args[:2] == ['pr', 'checks']:
    jq = args[args.index('--jq') + 1]
    with open({checks!r}) as f:
        sys.stdout.write(subprocess.run(['jq', '-r', jq], stdin=f, check=True,
                                        capture_output=True, text=True).stdout)
'''


def check(bucket, name, workflow='server', event='pull_request'):
    return {'bucket': bucket, 'name': name, 'link': f'https://example/{name}',
            'workflow': workflow, 'event': event}


@unittest.skipUnless(shutil.which('jq'), 'jq runs the fake gh\'s --jq filter')
class PrWait(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def wait(self, checks, pr='7'):
        (self.tmp / 'checks.json').write_text(json.dumps(checks))
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, pr=pr,
                                     checks=str(self.tmp / 'checks.json')))
        gh.chmod(0o755)
        return subprocess.run(['bash', str(PR_WAIT), pr, '--timeout-min', '0'],
                              env={**os.environ, 'GH': str(gh)},
                              capture_output=True, text=True, timeout=60)

    def test_a_red_parity_push_run_is_printed_but_not_counted(self):
        """#295: its parity-check push run's `leg (django, *)` jobs were red,
        pr-wait exited 1, and pr-merge, which leaves them out, merged it."""
        done = self.wait([check('pass', 'lint'),
                          check('fail', 'leg (django, sqlite)', 'parity', 'push'),
                          check('pass', 'gate (django, sqlite)', 'parity', 'push')])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        counted, _, parity = done.stdout.partition('parity-check push (not counted):\n')
        self.assertEqual(counted, 'pass lint https://example/lint\n')
        self.assertIn('  fail leg (django, sqlite) https://example/leg', parity)

    def test_a_pending_parity_push_run_is_not_waited_for(self):
        done = self.wait([check('pass', 'lint'),
                          check('pending', 'leg (php)', 'parity', 'push')])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_red_parity_run_on_a_schedule_still_counts(self):
        """Only the push runs are left out, as in pr-merge."""
        done = self.wait([check('pass', 'lint'),
                          check('fail', 'leg (php)', 'parity', 'schedule')])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('fail leg (php)', done.stdout.split('ci-failures:')[0])

    def test_a_red_pull_request_check_fails(self):
        done = self.wait([check('fail', 'lint'), check('pass', 'ui (sqlite)')])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertNotIn('parity-check push', done.stdout)

    def test_a_branch_names_its_pr(self):
        """#58's implementer waited on image.yml alone, and missed a red
        server.yml (#58 retro)."""
        done = self.wait([check('pass', 'build', 'image'), check('fail', 'pytest (sqlite)')],
                         pr='issue-58-docker-image')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('fail pytest (sqlite)', done.stdout)

    def test_a_pending_pull_request_check_is_waited_for(self):
        done = self.wait([check('pending', 'lint'),
                          check('pass', 'leg (php)', 'parity', 'push')])
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
