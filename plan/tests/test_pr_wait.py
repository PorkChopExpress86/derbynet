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

import k_patterns  # noqa: F401  a -k that matches no test is refused

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


# Answers each `pr view` with the next head in HEADS, and the last for
# ever after, and `pr checks` with the checks of the head it last answered.
FAKE_GH_HEADS = '''#!{python}
import pathlib, sys
args = sys.argv[1:]
calls = pathlib.Path({calls!r})
heads = {heads!r}
if args[:2] == ['pr', 'view']:
    n = int(calls.read_text()) if calls.exists() else 0
    calls.write_text(str(n + 1))
    print(heads[min(n, len(heads) - 1)], {branch!r})
elif args[:2] == ['pr', 'checks']:
    n = int(calls.read_text())
    head = heads[min(n - 1, len(heads) - 1)]
    print({checks!r}[head])
'''


@unittest.skipUnless(shutil.which('git'), 'the clone that pushed is a git repository')
class PushedHead(unittest.TestCase):
    """A pr-wait started as a push ends waits for the PR to show the pushed
    head.  #384's pr-wait read the old head 559330bc, whose checks had all
    passed (#369 retro)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.clone = self.tmp / 'clone'
        self.clone.mkdir()
        # A pre-push hook runs this with GIT_DIR and the like set for the
        # repository being pushed; the scratch clone and pr-wait take none.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q')
        self.old = self.commit('old')
        self.new = self.commit('new')

    def git(self, *args):
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              cwd=self.clone, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def wait(self, heads, origin):
        """pr-wait 7 on branch issue-7, whose origin/issue-7 is `origin`, with
        the old head's checks all passed and the new head's lint failed."""
        if origin:
            self.git('update-ref', 'refs/remotes/origin/issue-7', origin)
        gh = self.gh(heads, {self.old: 'pass lint https://example/lint',
                             self.new: 'fail lint https://example/lint'})
        return self.run_pr_wait(gh, timeout_min='1')

    def gh(self, heads, checks):
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH_HEADS.format(python=sys.executable, heads=heads,
                                           branch='issue-7', checks=checks,
                                           calls=str(self.tmp / 'calls')))
        gh.chmod(0o755)
        return gh

    def run_pr_wait(self, gh, timeout_min, **env):
        return subprocess.run(['bash', str(PR_WAIT), '7', '--timeout-min', timeout_min],
                              cwd=self.clone, capture_output=True, text=True, timeout=60,
                              env={**self.env, 'GH': str(gh), 'PR_WAIT_RETRY_SLEEP': '0', **env})

    def fake_clock(self, seconds=60):
        """A `date` that moves `seconds` on each call, first in PATH."""
        bin_dir = self.tmp / 'bin'
        bin_dir.mkdir()
        date = bin_dir / 'date'
        date.write_text(f'#!{sys.executable}\nimport pathlib\n'
                        f'f = pathlib.Path({str(self.tmp / "ticks")!r})\n'
                        'n = int(f.read_text()) if f.exists() else 0\n'
                        f'f.write_text(str(n + 1))\nprint(1000000 + {seconds} * n)\n')
        date.chmod(0o755)
        return {'PATH': f'{bin_dir}:{self.env["PATH"]}'}

    def test_the_old_head_is_waited_past_until_the_pr_shows_the_push(self):
        done = self.wait([self.old, self.old, self.old, self.new], origin=self.new)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'PR #7 head {self.new}', done.stderr)
        self.assertIn('fail lint', done.stdout)

    def test_a_stale_origin_branch_is_not_waited_for(self):
        """origin/issue-7 older than the PR's head: this clone did not push it."""
        done = self.wait([self.new], origin=self.old)
        self.assertIn(f'PR #7 head {self.new}', done.stderr)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_a_clone_without_the_branch_takes_the_prs_head(self):
        done = self.wait([self.old], origin=None)
        self.assertIn(f'PR #7 head {self.old}', done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_pr_that_never_shows_the_push_times_out_naming_both(self):
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old], {}), timeout_min='0')
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn(f'still shows {self.old}, older than origin/issue-7 {self.new}',
                      done.stderr)

    def test_the_wait_for_the_push_says_so_when_it_starts(self):
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old, self.old, self.new], {self.new: 'pass lint u'}),
                                timeout_min='90')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr.count('waiting for PR #7 to show'), 1, done.stderr)
        self.assertIn(f'waiting for PR #7 to show origin/issue-7 {self.new}; it shows {self.old}',
                      done.stderr)

    def test_a_pr_at_the_pushed_head_prints_no_wait_line(self):
        done = self.wait([self.new], origin=self.new)
        self.assertNotIn('waiting for PR', done.stderr)

    def test_the_wait_for_the_push_gives_up_after_five_minutes(self):
        """A PR reset to an older commit than origin/<branch> shows that
        commit for ever; the 90 minute default must not apply (#396)."""
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old], {}), timeout_min='90',
                                **self.fake_clock())
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn(f'timed out after 5 min; PR #7 still shows {self.old}, '
                      f'older than origin/issue-7 {self.new}', done.stderr)
        self.assertLess(int((self.tmp / 'ticks').read_text()), 12)

    def test_the_push_bound_is_set_by_pr_wait_push_min(self):
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old], {}), timeout_min='90',
                                PR_WAIT_PUSH_MIN='2', **self.fake_clock())
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn('timed out after 2 min', done.stderr)

    def test_the_wait_for_the_push_ends_the_moment_its_bound_is_reached(self):
        """With no minute passing, a bound of 0 min is reached at once."""
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old], {}), timeout_min='90',
                                PR_WAIT_PUSH_MIN='0', **self.fake_clock(seconds=0))
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn('timed out after 0 min', done.stderr)

    def test_the_push_bound_never_exceeds_the_timeout(self):
        self.git('update-ref', 'refs/remotes/origin/issue-7', self.new)
        done = self.run_pr_wait(self.gh([self.old], {}), timeout_min='1',
                                PR_WAIT_PUSH_MIN='50', **self.fake_clock())
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn('timed out after 1 min', done.stderr)

    def test_a_pr_gh_cannot_read_fails_naming_it(self):
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\nimport sys\nsys.exit(1)\n')
        gh.chmod(0o755)
        done = self.run_pr_wait(gh, timeout_min='1')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('gh could not read PR #7 after 5 tries', done.stderr)
        self.assertNotIn('PR #7 head', done.stderr)


if __name__ == '__main__':
    unittest.main()
