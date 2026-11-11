"""plan/bin/ci-failures against a fake gh, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI_FAILURES = Path(__file__).resolve().parent.parent / 'bin' / 'ci-failures'
VOTE = 'ui/tests/browser/test_vote.py::test_the_sort_control_orders_the_grid_and_keeps_the_url[desktop-chromium]'
EVENTS = 'ui/tests/browser/test_events.py::test_reopen_and_archive_from_the_page[desktop-chromium]'


def line(test, reason=''):
    return f'ui (postgres)\tRun the browser tests\t2026-11-06T18:34:35Z [gw3] [ 87%] FAILED {test}{reason}\n'


def run(checks, logs, master):
    """`checks`: the PR's checks; `logs`: run id -> its failed log;
    `master`: the failed master runs, newest first."""
    with tempfile.TemporaryDirectory() as tmp:
        gh = Path(tmp) / 'gh'
        gh.write_text(f'''#!{sys.executable}
import json, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'checks']:
    print(json.dumps({checks!r}))
elif args[:2] == ['run', 'list']:
    assert '--branch' in args and args[args.index('--branch') + 1] == 'master'
    print(json.dumps({master!r}))
elif args[:2] == ['run', 'view']:
    print({logs!r}[args[2]], end='')
else:
    sys.exit('unexpected gh ' + ' '.join(args))
''')
        gh.chmod(0o755)
        return subprocess.run([sys.executable, '-I', str(CI_FAILURES), '219'],
                              env={**os.environ, 'GH': str(gh)},
                              capture_output=True, text=True)


def check(bucket, run_id, name='ui (postgres)'):
    return {'bucket': bucket, 'name': name,
            'link': f'https://github.com/o/r/actions/runs/{run_id}/job/9{run_id}'}


def apt(job, step, *texts, bom=''):
    """Log lines as `gh run view --log-failed` prints them: a run's first
    line carries a byte-order mark before its timestamp, and apt's output
    carries colour codes."""
    return ''.join(f'{job}\t{step}\t{bom}2026-11-11T16:47:38.8712844Z \x1b[36;1m{t}\x1b[0m\n'
                   for t in texts)


# A failed apt step, and the log dump that fails after it.
APT_LOG = (
    apt('ui (sqlite)', 'Install Chromium', 'Reading package lists...', bom='﻿')
    # Printed more often than the shared line, by one job only.
    + apt('ui (sqlite)', 'Install Chromium',
        *['W: Some index files failed to download. They have been ignored.'] * 4)
    + apt('ui (sqlite)', 'Install Chromium',
        "Err:7 https://security.ubuntu.com/ubuntu noble/main amd64 xfonts-encodings all 1:1.0.5-0ubuntu2",
        "  Temporary failure resolving 'azure.archive.ubuntu.com'",
        "Err:8 https://security.ubuntu.com/ubuntu noble/main amd64 xfonts-utils amd64 1:7.7+6build3",
        "  Temporary failure resolving 'azure.archive.ubuntu.com'",
        'E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?',
        'Failed to install browsers',
        'Error: Installation process exited with code: 100',
        '##[error]Process completed with exit code 1.')
    + apt('shell (sqlite, group 1)', 'Start the Django server',
        'W: Some index files failed to download. They have been ignored, or old ones used instead.',
        "Err:2 mirror+file:/etc/apt/apt-mirrors.txt noble-updates/main amd64 libxml2-utils amd64",
        "  Temporary failure resolving 'azure.archive.ubuntu.com'",
        'E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?',
        '##[error]Process completed with exit code 100.')
    + apt('shell (sqlite, group 1)', 'Show the Django server log',
        "cat: var/server.log: No such file or directory",
        '##[error]Process completed with exit code 1.')
)


MASTER = [{'databaseId': 1, 'headSha': '0141fa3b6ae9c6fac7092f31a2f129a7761349f5'}]


class CiFailures(unittest.TestCase):
    def test_a_test_that_failed_on_master_is_known(self):
        """#212: test_vote's sort test failed on the PR and on master's
        push of 0141fa3b (#212 retro)."""
        result = run([check('fail', 2), check('pass', 2, 'ui (sqlite)')],
                     {'2': line(VOTE) + f'FAILED {VOTE} - assert [] == [...]\n',
                      '1': line(VOTE)}, MASTER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f'known  {VOTE}  master 0141fa3b run 1\n')

    def test_a_test_master_never_failed_is_new(self):
        result = run([check('fail', 2)], {'2': line(VOTE) + line(EVENTS), '1': line(VOTE)},
                     MASTER)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f'new    {EVENTS}\n', result.stdout)
        self.assertIn(f'known  {VOTE}  master 0141fa3b run 1\n', result.stdout)

    def test_a_failed_check_with_no_test_id_names_its_step_and_error(self):
        lint = ('lint\tRun ruff\t2026-11-11T16:41:34.3875028Z E501 Line too long (110 > 88)\n'
                'lint\tRun ruff\t2026-11-11T16:41:34.3875028Z Found 1 error.\n'
                'lint\tRun ruff\t2026-11-11T16:41:34.3875028Z ##[error]Process completed with exit code 1.\n')
        result = run([check('fail', 3, 'lint')], {'3': lint}, [])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout,
                         'no test id  run 3: 1 job\n  lint  step "Run ruff": Found 1 error.\n')

    def test_jobs_that_failed_at_setup_name_the_error_they_share(self):
        """PR #284's run 43054997286 failed jobs at apt in different steps,
        and the lead read the logs by hand to find the mirror they all timed
        out on (#282 retro). Two of its jobs, with lines taken from that log,
        some shortened."""
        result = run([check('fail', 4, 'ui (sqlite)'), check('fail', 4, 'shell (sqlite, group 1)')],
                     {'4': APT_LOG}, [])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, (
            'no test id  run 4: 2 jobs\n'
            '  ui (sqlite)  step "Install Chromium": Error: Installation process exited with code: 100\n'
            '  shell (sqlite, group 1)  step "Start the Django server": '
            'E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?\n'
            "  2 of 2 jobs printed: Temporary failure resolving 'azure.archive.ubuntu.com'\n"))

    def test_a_job_that_failed_at_setup_beside_a_failed_test_is_reported(self):
        log = line(VOTE) + APT_LOG
        result = run([check('fail', 5), check('fail', 5, 'ui (sqlite)')], {'5': log, '1': ''},
                     MASTER)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no test id  run 5: 2 jobs\n', result.stdout)
        self.assertIn(f'new    {VOTE}\n', result.stdout)

    def test_no_failed_check(self):
        result = run([check('pass', 2)], {}, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'PR #219: no failed check\n')


class PrWaitPrintsThem(unittest.TestCase):
    def test_a_failed_check_prints_the_known_flake(self):
        checks = [check('fail', 2)]
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp) / 'gh'
            gh.write_text(f'''#!{sys.executable}
import json, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view']:
    print('abc123')
elif args[:2] == ['pr', 'checks'] and '--jq' in args:
    for c in {checks!r}:
        print(c['bucket'], c['name'], c['link'])
elif args[:2] == ['pr', 'checks']:
    print(json.dumps({checks!r}))
elif args[:2] == ['run', 'list']:
    print(json.dumps({MASTER!r}))
elif args[:2] == ['run', 'view']:
    print({line(VOTE)!r}, end='')
''')
            gh.chmod(0o755)
            result = subprocess.run(['bash', str(CI_FAILURES.parent / 'pr-wait'), '219'],
                                    env={**os.environ, 'GH': str(gh)},
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f'ci-failures:\nknown  {VOTE}  master 0141fa3b run 1\n', result.stdout)


if __name__ == '__main__':
    unittest.main()
