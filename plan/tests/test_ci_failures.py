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

    def test_a_failed_check_with_no_test_id_says_to_read_its_log(self):
        result = run([check('fail', 3, 'lint')], {'3': 'ruff: E501 line too long\n'}, [])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no test id  run 3 (lint): read its log', result.stdout)

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
