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
            "  2 of 2 jobs printed: Temporary failure resolving 'azure.archive.ubuntu.com'\n"
            'outage: every failure is in a setup step, before any test; probe the dependency '
            'the shared line names, and work around it in a PR of its own or report it '
            '(plan/implement-all-tickets.md, Stop and report)\n'))

    def test_a_job_that_failed_at_setup_beside_a_failed_test_is_reported(self):
        log = line(VOTE) + APT_LOG
        result = run([check('fail', 5), check('fail', 5, 'ui (sqlite)')], {'5': log, '1': ''},
                     MASTER)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no test id  run 5: 2 jobs\n', result.stdout)
        self.assertIn(f'new    {VOTE}\n', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_jobs_that_share_a_line_outside_setup_are_no_outage(self):
        """Two jobs that fail the same lint step print the same line, but a
        lint step is no setup step."""
        lint = ''.join(apt(job, 'Run ruff', 'E501 Line too long (110 > 88)', 'Found 1 error.',
                           '##[error]Process completed with exit code 1.')
                       for job in ('lint (a)', 'lint (b)'))
        result = run([check('fail', 6, 'lint (a)'), check('fail', 6, 'lint (b)')],
                     {'6': lint}, [])
        self.assertIn('2 of 2 jobs printed', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_a_network_line_outside_setup_is_no_outage(self):
        """Tests that lose a connection fail in a test step, which no step
        rule calls setup."""
        log = ''.join(apt(job, 'Run the browser tests', 'Error: Connection refused',
                          '##[error]Process completed with exit code 1.')
                      for job in ('ui (sqlite)', 'ui (postgres)'))
        result = run([check('fail', 15, 'ui (sqlite)'), check('fail', 15, 'ui (postgres)')],
                     {'15': log}, [])
        self.assertIn('2 of 2 jobs printed: Error: Connection refused', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_a_build_step_that_compares_is_no_setup_step(self):
        log = APT_LOG.replace('Install Chromium', 'Build the CSS offline, with no Node, and compare it')
        result = run([check('fail', 7, 'ui (sqlite)'), check('fail', 7, 'shell (sqlite, group 1)')],
                     {'7': log}, [])
        self.assertIn('2 of 2 jobs printed', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_each_setup_step_name_counts(self):
        for step in ('Set up job', 'Install dependencies', 'Fetch the pinned Tailwind CLI',
                     'Start the Django server', 'Build the test harness image',
                     'Initialize containers', 'Checkout', 'Run actions/checkout@v4'):
            with self.subTest(step=step):
                log = APT_LOG.replace('Install Chromium', step)
                result = run([check('fail', 8, 'ui (sqlite)'),
                              check('fail', 8, 'shell (sqlite, group 1)')], {'8': log}, [])
                self.assertIn('outage:', result.stdout)

    def test_a_line_fewer_than_every_job_printed_is_no_outage(self):
        third = apt('pytest (sqlite)', 'Install dependencies',
                    "E: Package 'libxml2-utils' has no installation candidate",
                    '##[error]Process completed with exit code 100.')
        result = run([check('fail', 9, 'ui (sqlite)'), check('fail', 9, 'shell (sqlite, group 1)'),
                      check('fail', 9, 'pytest (sqlite)')], {'9': APT_LOG + third}, [])
        self.assertIn('no test id  run 9: 3 jobs\n', result.stdout)
        self.assertIn('2 of 3 jobs printed', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_one_job_at_setup_is_no_outage(self):
        one = ''.join(ln + '\n' for ln in APT_LOG.splitlines() if ln.startswith('ui (sqlite)'))
        result = run([check('fail', 10, 'ui (sqlite)')], {'10': one}, [])
        self.assertIn('no test id  run 10: 1 job\n', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_a_failed_test_in_another_run_is_no_outage(self):
        result = run([check('fail', 4, 'ui (sqlite)'), check('fail', 4, 'shell (sqlite, group 1)'),
                      check('fail', 11)], {'4': APT_LOG, '11': line(VOTE), '1': ''}, MASTER)
        self.assertIn(f'new    {VOTE}\n', result.stdout)
        self.assertNotIn('outage', result.stdout)

    def test_a_shared_code_failure_at_setup_is_no_outage(self):
        """The Standards review of 4d459525: a stale uv.lock and conflicting
        migrations fail every job's setup with one shared line, and are code
        failures."""
        uv = ''.join(apt(job, 'Install dependencies',
                         'error: The lockfile at `uv.lock` needs to be updated, but `--locked` '
                         'was provided. To update the lockfile, run `uv lock`.',
                         '##[error]Process completed with exit code 2.')
                     for job in ('ui (sqlite)', 'pytest (sqlite)', 'lint'))
        migrations = ''.join(apt(job, 'Start the Django server',
                                 'CommandError: Conflicting migrations detected; multiple leaf '
                                 'nodes in the migration graph: (0012_a, 0012_b in core).',
                                 'Error: Process completed with exit code 1.',
                                 '##[error]Process completed with exit code 1.')
                             for job in ('shell (sqlite, group 1)', 'shell (postgres, group 1)'))
        for log, jobs in ((uv, ('ui (sqlite)', 'pytest (sqlite)', 'lint')),
                          (migrations, ('shell (sqlite, group 1)', 'shell (postgres, group 1)'))):
            with self.subTest(jobs=jobs):
                result = run([check('fail', 12, job) for job in jobs], {'12': log}, [])
                self.assertIn(f'{len(jobs)} of {len(jobs)} jobs printed', result.stdout)
                self.assertNotIn('outage', result.stdout)

    def test_each_network_line_counts(self):
        for text in ("Temporary failure resolving 'azure.archive.ubuntu.com'",
                     'curl: (6) Could not resolve host: github.com',
                     'Connection timed out', 'Connection refused', 'Connection reset by peer',
                     'E: Failed to fetch http://archive.ubuntu.com/a.deb',
                     'Error: HTTP 502 from the registry', 'Error: 503 Service Unavailable',
                     'Error: API rate limit exceeded', 'Error: TLS handshake timeout'):
            with self.subTest(text=text):
                log = ''.join(apt(job, 'Install dependencies', text,
                                  '##[error]Process completed with exit code 1.')
                              for job in ('ui (sqlite)', 'pytest (sqlite)'))
                result = run([check('fail', 13, 'ui (sqlite)'), check('fail', 13, 'pytest (sqlite)')],
                             {'13': log}, [])
                self.assertIn('outage:', result.stdout)

    def test_a_failed_check_with_no_run_is_no_outage(self):
        """A failed check whose link names no run (an external status) gives
        no run to call an outage."""
        result = run([{'bucket': 'fail', 'name': 'external', 'link': 'https://example.com/x'}],
                     {}, [])
        self.assertNotIn('outage', result.stdout)

    def test_a_run_with_an_empty_log_is_no_outage(self):
        result = run([check('fail', 14, 'ui (sqlite)')], {'14': ''}, [])
        self.assertIn('its log is empty', result.stdout)
        self.assertNotIn('outage', result.stdout)

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
