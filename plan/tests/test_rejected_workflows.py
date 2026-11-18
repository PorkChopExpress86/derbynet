"""plan/bin/rejected-workflows against a fake gh that answers the runs API
with real responses run through jq with the tool's own --jq filters, so the
filters are tested.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

BIN = Path(__file__).resolve().parent.parent / 'bin'
REJECTED = BIN / 'rejected-workflows'
PR_WAIT = BIN / 'pr-wait'

FF00 = 'ff00bbc6e64593f2c41b54e1f9e702a7455fd431'
RUNS_URL = 'https://github.com/PorkChopExpress86/derbynet/actions/runs/'
# GitHub's runs on #59's head ff00bbc6, fields trimmed: pi-images.yml put
# `${{ runner.temp }}` in a job's env, and GitHub rejected the file.
FF00_RUNS = [
    {'id': 44236605187, 'path': '.github/workflows/plan-checks.yml',
     'event': 'pull_request', 'status': 'completed', 'conclusion': 'success'},
    {'id': 44236605179, 'path': '.github/workflows/image.yml',
     'event': 'pull_request', 'status': 'completed', 'conclusion': 'success'},
    {'id': 44236605153, 'path': '.github/workflows/server.yml',
     'event': 'pull_request', 'status': 'completed', 'conclusion': 'success'},
    {'id': 44236545824, 'path': '.github/workflows/pi-images.yml',
     'event': 'push', 'status': 'completed', 'conclusion': 'failure'},
]
# Each run's job count, as runs/<id>/jobs answers: the rejected run had none,
# and a0cb60fa's failed pi-images run 44246355493 had 3.
JOBS = {44236545824: 0, 44236605187: 1, 44236605179: 11, 44236605153: 17,
        44246355493: 3}
REJECTED_LINE = f'rejected .github/workflows/pi-images.yml {RUNS_URL}44236545824'

# Answers `api` from RUNS and JOBS through the caller's --jq; `pr view` and
# `pr checks` (for pr-wait) from HEAD and CHECKS.
FAKE_GH = '''#!{python}
import json, subprocess, sys
args = sys.argv[1:]
runs, jobs, head, checks = {runs!r}, {jobs!r}, {head!r}, {checks!r}

def jq(data):
    filt = args[args.index('--jq') + 1]
    sys.stdout.write(subprocess.run(['jq', '-r', filt], input=json.dumps(data),
                                    check=True, capture_output=True, text=True).stdout)

if args[0] == 'api':
    path = args[1]
    if '/jobs' in path:
        jq({{'total_count': jobs[int(path.split('/')[-2])]}})
    else:
        assert path.endswith('runs?head_sha=' + head + '&per_page=100'), path
        jq({{'total_count': len(runs), 'workflow_runs': runs}})
elif args[:2] == ['pr', 'view']:
    print(head, 'no-such-branch')
elif args[:2] == ['pr', 'checks']:
    jq(checks)
'''


def run(status, conclusion, run_id=44236545824):
    return {'id': run_id, 'path': '.github/workflows/pi-images.yml', 'event': 'push',
            'status': status, 'conclusion': conclusion}


@unittest.skipUnless(shutil.which('jq'), "jq runs the fake gh's --jq filters")
class RejectedWorkflows(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def gh(self, runs, head=FF00, checks=()):
        runs = [{**r, 'html_url': f'{RUNS_URL}{r["id"]}'} for r in runs]
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, runs=runs, jobs=JOBS,
                                     head=head, checks=list(checks)))
        gh.chmod(0o755)
        return {**os.environ, 'GH': str(gh), 'PR_WAIT_RETRY_SLEEP': '0'}

    def rejected(self, runs, head=FF00):
        return subprocess.run(['bash', str(REJECTED), head], env=self.gh(runs, head),
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)

    def test_ff00bbc6s_rejected_pi_images_run_is_named(self):
        done = self.rejected(FF00_RUNS)
        self.assertEqual((done.returncode, done.stdout), (0, REJECTED_LINE + '\n'),
                         done.stderr)

    def test_a_failed_run_with_jobs_is_left_to_its_checks(self):
        """a0cb60fa's pi-images run failed in its build job, which
        `gh pr checks` lists as failed."""
        done = self.rejected([run('completed', 'failure', 44246355493)],
                             head='a0cb60fab24b19b133c52e44d35b82c93ad3e6de')
        self.assertEqual((done.returncode, done.stdout), (0, ''), done.stderr)

    def test_a_startup_failure_with_no_job_is_named(self):
        done = self.rejected([run('completed', 'startup_failure')])
        self.assertEqual(done.stdout, REJECTED_LINE + '\n', done.stderr)

    def test_a_run_with_no_job_that_did_not_fail_is_not_named(self):
        """Cancelled, still queued, or passed, with the job count of the
        rejected run: none."""
        results = []
        for status, conclusion in (('completed', 'cancelled'), ('queued', None),
                                   ('completed', 'success')):
            done = self.rejected([run(status, conclusion)])
            results.append((status, conclusion, done.returncode, done.stdout))
        self.assertEqual(results, [('completed', 'cancelled', 0, ''), ('queued', None, 0, ''),
                                   ('completed', 'success', 0, '')])

    def test_no_run_names_nothing(self):
        done = self.rejected([])
        self.assertEqual((done.returncode, done.stdout), (0, ''), done.stderr)

    def test_a_read_gh_fails_exits_1_naming_it(self):
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\nimport sys\nsys.exit(1)\n')
        gh.chmod(0o755)
        done = subprocess.run(['bash', str(REJECTED), FF00],
                              env={**os.environ, 'GH': str(gh), 'PR_WAIT_RETRY_SLEEP': '0'},
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual((done.returncode, done.stdout), (1, ''))
        self.assertIn('gh could not read repos/PorkChopExpress86/derbynet/actions/runs'
                      f'?head_sha={FF00}&per_page=100 after 3 tries', done.stderr)

    def test_a_jobs_read_that_fails_exits_1(self):
        jobs = dict(JOBS)
        del jobs[44236545824]  # the fake gh's lookup raises: gh exits 1
        env = self.gh(FF00_RUNS)
        gh = Path(env['GH'])
        gh.write_text(gh.read_text().replace(repr(JOBS), repr(jobs)))
        done = subprocess.run(['bash', str(REJECTED), FF00], env=env,
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual((done.returncode, done.stdout), (1, ''))
        self.assertIn('runs/44236545824/jobs after 3 tries', done.stderr)

    def test_pr_wait_fails_on_ff00bbc6_with_every_listed_check_passed(self):
        """pr-wait exited 0 on this head: no check of pi-images showed on the
        PR, and the rest had passed (#59 retro)."""
        checks = [{'bucket': 'pass', 'name': name, 'link': f'https://example/{name}',
                   'workflow': name, 'event': 'pull_request'}
                  for name in ('plan-checks', 'image', 'server')]
        done = subprocess.run(['bash', str(PR_WAIT), '403', '--timeout-min', '0'],
                              env=self.gh(FF00_RUNS, checks=checks),
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual((done.returncode, done.stdout), (1, REJECTED_LINE + '\n'),
                         done.stderr)
        self.assertIn(f'GitHub rejected each workflow file above on {FF00}', done.stderr)

    def test_pr_wait_passes_ff00bbc6s_runs_without_the_rejected_one(self):
        checks = [{'bucket': 'pass', 'name': 'server', 'link': 'https://example/server',
                   'workflow': 'server', 'event': 'pull_request'}]
        done = subprocess.run(['bash', str(PR_WAIT), '403', '--timeout-min', '0'],
                              env=self.gh(FF00_RUNS[:3], checks=checks),
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual((done.returncode, done.stdout),
                         (0, 'pass server https://example/server\n'), done.stderr)

    def test_pr_wait_fails_when_it_cannot_read_the_runs(self):
        checks = [{'bucket': 'pass', 'name': 'server', 'link': 'https://example/server',
                   'workflow': 'server', 'event': 'pull_request'}]
        env = self.gh(FF00_RUNS, checks=checks)
        gh = Path(env['GH'])
        gh.write_text(gh.read_text().replace("if args[0] == 'api':",
                                             "if args[0] == 'api':\n    sys.exit(1)"))
        done = subprocess.run(['bash', str(PR_WAIT), '403', '--timeout-min', '0'], env=env,
                              capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'could not read the workflow runs on {FF00}', done.stderr)
        self.assertNotIn('pass server', done.stdout)


if __name__ == '__main__':
    unittest.main()
