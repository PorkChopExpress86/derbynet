"""plan/bin/pr-merge against a fake gh and a scratch repository with a bare
origin, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PR_MERGE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-merge'
SOURCE_CHECKS = Path(__file__).resolve().parents[2] / 'server' / 'tools' / 'source-checks.sh'

# Stand-ins for the two halves of server/tools/source-checks.sh, so no real
# check runs (as server/tests/test_source_checks.py stubs them): each logs
# where it ran, the merge's parents and files, then prints what the real tool
# prints and exits as $STUB_<NAME> says (pass, fail, broken, hang, or
# deaf: hang and ignore SIGTERM).
STUB = '''#!/bin/bash
name=$(basename "$0")
if [ "$name" = python3 ] && [ "$*" != "-I -m unittest discover -s ../plan/tests" ]; then
  exec {python} "$@"
fi
{{ echo "$name $PWD $(git log -1 --format=%P)"
   awk '/^SigIgn:/ {{ print "  SigIgn " $2 }}' /proc/$$/status; git -C "$(git rev-parse --show-toplevel)" ls-files | sed 's/^/  /'; }} >>"$RAN"
var=STUB_${{name^^}}
case $name:${{!var:-}} in
  python3:fail)
    printf '%s\\n' '.F..' '======' 'FAIL: test_z (test_q.T.test_z)' '------' \\
      'Ran 4 tests in 0.010s' '' 'FAILED (failures=1)'
    exit 1 ;;
  uv:fail)
    printf '%s\\n' '..F.' '=== short test summary info ===' \\
      'FAILED tests/test_x.py::test_y - assert 1 == 2' '1 failed, 3 passed in 0.12s'
    exit 1 ;;
  uv:broken)
    echo 'error: The lockfile at `uv.lock` needs to be updated'
    exit 2 ;;
  uv:hang)
    echo $$ >"$RAN.pid"
    while :; do date >>busy; sleep 0.1; done ;;
  uv:deaf)
    trap '' TERM
    echo $$ >"$RAN.pid"
    while :; do date >>busy; sleep 0.1; done ;;
esac
'''

# Every check passed (the --jq filter prints nothing) and the PR closes
# nothing, so only the base check can refuse. `pr merge` is logged.
FAKE_GH = '''#!{python}
import sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view'] and 'headRefOid' in args:
    print({head!r})
elif args[:2] == ['pr', 'view'] and 'state,mergeCommit' in args:
    print('MERGED abc')
elif args[:2] == ['pr', 'merge']:
    with open({log!r}, 'a') as f:
        f.write(' '.join(args) + '\\n')
'''


class PrMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                        GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1', CLOSE_WAIT='0')
        stubs = self.tmp / 'stubs'
        stubs.mkdir()
        for name in ('python3', 'uv'):
            (stubs / name).write_text(STUB.format(python=sys.executable))
            (stubs / name).chmod(0o755)
        self.ran_log = self.tmp / 'ran'
        self.cache = self.tmp / 'cache'
        self.env.update(PATH=f'{stubs}:{os.environ["PATH"]}', RAN=str(self.ran_log),
                        XDG_CACHE_HOME=str(self.cache))
        self.work = self.tmp / 'work'
        self.git('init', '-q', '--bare', '-b', 'master', str(self.tmp / 'origin.git'), cwd=self.tmp)
        self.git('clone', '-q', str(self.tmp / 'origin.git'), str(self.work), cwd=self.tmp)
        self.commit('base', 'plan/README.md', 'server/ui/kiosk.css', 'server/conftest.py')
        script = self.work / 'server' / 'tools' / 'source-checks.sh'
        script.parent.mkdir(parents=True)
        shutil.copy(SOURCE_CHECKS, script)
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'the real source checks')
        self.git('push', '-q', 'origin', 'HEAD:master')
        self.git('switch', '-q', '-c', 'branch')
        self.log = self.tmp / 'merges'

    def git(self, *argv, cwd=None):
        return subprocess.run(['git', *argv], cwd=cwd or self.work, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message, *paths):
        for rel in paths:
            path = self.work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'{message}\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def pr(self, *paths):
        """Commits `paths` on the branch as PR 7's head, pushed where GitHub
        keeps it."""
        head = self.commit('the PR', *paths)
        self.git('push', '-q', 'origin', 'HEAD:refs/pull/7/head')
        return head

    def master(self, *paths):
        """Commits `paths` on origin/master after the PR's merge base."""
        self.git('switch', '-q', '-C', 'moved', 'origin/master')
        self.commit('#288: master moved', *paths)
        self.git('push', '-q', 'origin', 'HEAD:master')
        self.git('switch', '-q', 'branch')

    def merge(self, head, *argv):
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, head=head, log=str(self.log)))
        gh.chmod(0o755)
        return subprocess.run(['bash', str(PR_MERGE), '7', *argv], cwd=self.work,
                              env={**self.env, 'GH': str(gh)}, capture_output=True, text=True)

    def merged(self):
        return self.log.read_text() if self.log.exists() else ''

    def ran(self):
        """What the stand-ins logged: for each half that ran, '<name> <cwd>
        <parents>', then the files the merge has."""
        return self.ran_log.read_text() if self.ran_log.exists() else ''

    def assert_no_worktree(self):
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1,
                         self.git('worktree', 'list'))
        left = list(self.cache.glob('derbynet-pr-merge.*')) if self.cache.exists() else []
        self.assertEqual(left, [])

    def passed(self):
        path = self.work / '.git' / 'pr-merge-source-checks'
        return path.read_text() if path.exists() else ''

    def check_pair(self):
        """A PR whose code master's new source check reads: the source-check
        rule fires and the area rule does not."""
        head = self.pr('server/docs/adr/check-paths.sh')
        self.master('server/tests/test_php_claims.py')
        return head

    def test_master_moved_in_the_prs_area_refuses(self):
        """#289 changed plan/ and merged after #288, which changed plan/
        too, on CI that never saw #288."""
        head = self.pr('plan/AGENT-BRIEF.md', 'server/ui/kiosk.css')
        self.master('plan/bin/affected-tests')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.merged(), '')
        self.assertIn('not merging #7; origin/master moved since its merge base', done.stderr)
        self.assertIn('#288: master moved', done.stderr)
        self.assertIn('plan/\n  master:  plan/bin/affected-tests\n  this PR: plan/AGENT-BRIEF.md\n',
                      done.stderr)
        self.assertNotIn('kiosk.css', done.stderr.split('For the combined diff')[0])
        self.assertIn('--base-checked', done.stderr)
        self.assertIn('affected-tests: 3 changed files (stdin)', done.stderr)

    def test_base_checked_merges_and_is_not_passed_to_gh(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.master('plan/bin/affected-tests')
        done = self.merge(head, '--base-checked')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.merged(),
                         f'pr merge 7 --repo PorkChopExpress86/derbynet --match-head-commit {head} '
                         '--squash --delete-branch\n')

    def test_base_checked_keeps_a_merge_method(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.master('plan/bin/affected-tests')
        done = self.merge(head, '--rebase', '--base-checked')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(self.merged().endswith(f'{head} --rebase\n'), self.merged())

    def test_master_moved_elsewhere_merges(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.master('server/ui/kiosk.css', 'server/tools/x.py')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_master_not_moved_merges(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_each_area_refuses(self):
        for pr, master, area in (
                ('.githooks/pre-push', '.githooks/commit-msg', '.githooks/'),
                ('server/tools/a.py', 'server/tools/b.sh', 'server/tools/'),
                ('.github/workflows/server.yml', '.github/ubuntu-mirror.sh', '.github/'),
                ('server/ui/tests/conftest.py', 'server/conftest.py', 'conftest.py'),
                ('server/pyproject.toml', 'server/pyproject.toml', 'server/pyproject.toml'),
                ('server/uv.lock', 'server/uv.lock', 'server/uv.lock')):
            with self.subTest(area=area):
                self.setUp()
                head = self.pr(pr)
                self.master(master)
                done = self.merge(head)
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertIn(f'\n{area}\n  master:  {master}\n  this PR: {pr}\n', done.stderr)
                self.assertEqual(self.merged(), '')

    def test_areas_compare_the_same_in_any_locale(self):
        """en_US.UTF-8 sorts conftest.py before .github/, as comm does not:
        #295's merge printed "comm: file 1 is not in sorted order" twice, and
        an area both sides changed could be missed (#206 retro)."""
        for locale in ('en_US.UTF-8', 'C.UTF-8', 'C'):
            with self.subTest(locale=locale):
                self.setUp()
                self.env['LC_ALL'] = locale
                head = self.pr('server/ui/tests/conftest.py', '.github/workflows/server.yml',
                               'plan/README.md')
                self.master('.github/ubuntu-mirror.sh')
                done = self.merge(head)
                self.assertNotIn('comm:', done.stderr)
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertIn('\n.github/\n  master:  .github/ubuntu-mirror.sh\n', done.stderr)
                self.assertEqual(self.merged(), '')

    def test_master_moved_from_another_clone_is_fetched(self):
        """The local origin/master is stale: #288 merged from elsewhere. The
        Standards review of 4d459525 dropped the fetch and every other test
        here passed."""
        head = self.pr('plan/AGENT-BRIEF.md')
        other = self.tmp / 'other'
        self.git('clone', '-q', str(self.tmp / 'origin.git'), str(other), cwd=self.tmp)
        (other / 'plan' / 'bin').mkdir(parents=True, exist_ok=True)
        (other / 'plan' / 'bin' / 'affected-tests').write_text('#288\n')
        self.git('add', '.', cwd=other)
        self.git('commit', '-q', '-m', '#288: master moved', cwd=other)
        self.git('push', '-q', 'origin', 'HEAD:master', cwd=other)
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('plan/\n  master:  plan/bin/affected-tests\n', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_a_moved_path_with_a_space_names_its_commit(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.master('plan/Quick Reference.md')
        done = self.merge(head)
        self.assertIn('#288: master moved\nplan/\n', done.stderr)

    def source_checks(self, *tests):
        """Commits on the branch a server/tools/source-checks.sh that runs
        `tests` (paths under server/), as the real one lists them, and
        pushes it to master as the merge base."""
        lines = ''.join(f'  {t} \\\n' for t in tests)
        path = self.work / 'server' / 'tools' / 'source-checks.sh'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'exec uv run pytest -q \\\n{lines}  "$@"\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'source checks')
        self.git('push', '-q', 'origin', 'HEAD:master')

    def test_a_source_check_master_added_runs_on_a_pr_in_no_area(self):
        """#304 added test_pipefail_pipes.py to the source checks while #309
        added pipes to check-paths.sh, which no area holds. #309 also changed
        .github/, as #304 did; this PR changes nothing else (#61 retro). The
        checks run on the merge and pass, so it merges (#311)."""
        self.source_checks('tests/test_php_claims.py')
        head = self.pr('server/docs/adr/check-paths.sh')
        self.git('switch', '-q', '-c', 'moved', 'origin/master')
        self.commit('#304: pipefail rule', 'server/tests/test_pipefail_pipes.py')
        path = self.work / 'server' / 'tools' / 'source-checks.sh'
        path.write_text(path.read_text().replace(
            '  tests/test_php_claims.py', '  tests/test_php_claims.py \\\n  tests/test_pipefail_pipes.py'))
        self.git('commit', '-q', '-am', '#304: list it')
        self.git('push', '-q', 'origin', 'HEAD:master')
        self.git('switch', '-q', 'branch')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('pr merge 7', self.merged())
        self.assertIn('#304: pipefail rule\n', done.stderr)
        self.assertIn('a source check one side changed reads code the other changed:\n', done.stderr)
        self.assertNotIn('in an area the PR also changes', done.stderr)
        self.assertIn('  master check:  server/tests/test_pipefail_pipes.py\n'
                      '  master check:  server/tools/source-checks.sh\n'
                      '  this PR code:  server/docs/adr/check-paths.sh\n', done.stderr)
        self.assertIn('uv ', self.ran())
        self.assert_no_worktree()

    def test_the_real_source_check_list_is_read(self):
        """The list as server/tools/source-checks.sh writes it, not as the
        stand-in above does: each test it runs counts, its first, its last,
        and one named with `::` among them."""
        real = (Path(__file__).resolve().parents[2] / 'server' / 'tools'
                / 'source-checks.sh').read_text()
        listed = re.findall(r'^\s+([\w./-]+\.py)', real, re.M)
        self.assertIn('tests/test_pipefail_pipes.py', listed)
        for test in sorted({listed[0], listed[-1], 'tests/test_pipefail_pipes.py',
                            next(t for t in listed if 'test_htmx' in t)}):
            with self.subTest(test=test):
                self.setUp()  # which commits the real script
                head = self.pr('server/docs/adr/check-paths.sh')
                self.master(f'server/{test}')
                done = self.merge(head)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f'  master check:  server/{test}\n', done.stderr)
                self.assertIn('python3 ', self.ran())

    def test_a_source_check_the_pr_changes_runs_when_master_changed_code(self):
        self.source_checks('ui/tests/test_htmx.py::test_no_reply', 'tests/test_php_claims.py')
        head = self.pr('server/ui/tests/test_htmx.py')
        self.master('docs/install-docker.md', 'testing/common.sh')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('  this PR check: server/ui/tests/test_htmx.py\n'
                      '  master code:   testing/common.sh\n', done.stderr)
        self.assertIn('uv ', self.ran())

    def test_each_kind_of_code_counts(self):
        for code in ('server/ui/views.py', 'testing/test-basics.sh', '.github/workflows/x.yml',
                     '.githooks/pre-push', 'plan/bin/pr-wait'):
            with self.subTest(code=code):
                self.setUp()
                head = self.pr('plan/tests/test_lint_issues.py')
                self.master(code)
                done = self.merge(head)
                self.assertIn(f'  this PR check: plan/tests/test_lint_issues.py\n'
                              f'  master code:   {code}\n', done.stderr)
                self.assertIn('python3 ', self.ran())
                # plan/bin/pr-wait shares plan/ with the PR, so the area rule
                # refuses it once the checks pass.
                self.assertEqual(done.returncode, int(code.startswith('plan/')),
                                 done.stdout + done.stderr)

    def test_a_source_check_against_no_code_merges(self):
        """A template, CSS or guide change against a source check merges:
        some source checks read them (test_htmx.py, test_php_claims.py), and
        the rule leaves those to CI on master as a trade-off, since master
        changes a source check often (#61 retro review)."""
        for check, other in (('plan/tests/test_lint_issues.py', 'server/ui/kiosk.css'),
                             ('plan/tests/test_lint_issues.py', 'docs/install-docker.md'),
                             ('plan/tests/test_lint_issues.py', 'server/ui/templates/ui/a.html')):
            with self.subTest(other=other):
                self.setUp()
                head = self.pr(check)
                self.master(other)
                done = self.merge(head)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn('pr merge 7', self.merged())
                self.assertEqual(self.ran(), '')
                self.setUp()
                head = self.pr(other)
                self.master(check)
                done = self.merge(head)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.ran(), '')

    def test_a_source_check_on_one_side_with_master_not_moved_merges(self):
        self.source_checks('tests/test_php_claims.py')
        head = self.pr('server/tests/test_php_claims.py')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())
        self.assertEqual(self.ran(), '')

    def test_a_test_no_source_check_lists_is_no_source_check(self):
        self.source_checks('tests/test_php_claims.py')
        head = self.pr('server/ui/tests/test_docs.py')
        self.master('docs/install-docker.md')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())
        self.assertEqual(self.ran(), '')

    def test_base_checked_still_runs_the_source_checks(self):
        """--base-checked skips the area rule only (#311)."""
        head = self.pr('plan/tests/test_lint_issues.py')
        self.master('server/ui/views.py')
        self.env['STUB_UV'] = 'fail'
        done = self.merge(head, '--base-checked')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('server/tools/source-checks.sh failed', done.stderr)
        self.assertEqual(self.merged(), '')
        self.assertIn('uv ', self.ran())

    def test_a_failed_fetch_refuses_with_the_head_local(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.git('remote', 'set-url', 'origin', str(self.tmp / 'gone.git'))
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('could not fetch origin/master and its head', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_a_head_it_cannot_fetch_refuses(self):
        self.pr('plan/AGENT-BRIEF.md')
        done = self.merge('0' * 40)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('could not fetch origin/master and its head', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_a_passing_run_on_the_merge_merges(self):
        """Both halves of source-checks.sh run in a worktree of the PR's head
        merged with origin/master, which is gone afterwards (#311)."""
        head = self.check_pair()
        master = self.git('rev-parse', 'origin/master')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('pr merge 7', self.merged())
        ran = self.ran()
        for name in ('python3', 'uv'):
            line = next(r for r in ran.splitlines() if r.startswith(f'{name} '))
            cwd, parents = line.split(' ', 2)[1:]
            self.assertEqual(parents, f'{master} {head}')
            self.assertTrue(cwd.startswith(str(self.cache / 'derbynet-pr-merge.')), cwd)
            self.assertTrue(cwd.endswith('/server'), cwd)
        self.assertIn('  server/docs/adr/check-paths.sh\n', ran)
        # SIGINT is not ignored in the run, as in a run from a terminal:
        # bash ignores it in a background job, and its children inherit that.
        # A tool whose trap must see a Ctrl-C (this file's Ctrl-C tests,
        # run inside pr-merge's run on #311's own merge) hung there.
        for mask in re.findall(r'^  SigIgn ([0-9a-f]+)$', ran, re.M):
            self.assertFalse(int(mask, 16) & (1 << (signal.SIGINT - 1)), ran)
        self.assertEqual(len(re.findall(r'^  SigIgn ', ran, re.M)), 2, ran)
        self.assertIn('  server/tests/test_php_claims.py\n', ran)
        self.assertIn('the source checks passed on #7', done.stderr)
        self.assert_no_worktree()
        self.assertEqual(self.passed(), f'{head} {master}\n')
        self.assertEqual(list(self.cache.glob('*.log')), [])

    def test_a_failing_check_refuses_and_prints_its_summary_lines(self):
        """Each half's failures, in the formats unittest and pytest -q print
        them; the rest of the output stays in the log."""
        head = self.check_pair()
        self.env.update(STUB_PYTHON3='fail', STUB_UV='fail')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.merged(), '')
        log = self.cache / 'derbynet-pr-merge-7.log'
        self.assertIn('not merging #7; server/tools/source-checks.sh failed (exit 1) on the PR '
                      'merged with origin/master', done.stderr)
        self.assertIn(f'whole output in {log}:\n'
                      'FAIL: test_z (test_q.T.test_z)\n'
                      'Ran 4 tests in 0.010s\n'
                      'FAILED (failures=1)\n'
                      'FAILED tests/test_x.py::test_y - assert 1 == 2\n'
                      '1 failed, 3 passed in 0.12s\n', done.stderr)
        self.assertNotIn('short test summary', done.stderr)
        self.assertIn('short test summary', log.read_text())
        self.assert_no_worktree()
        self.assertEqual(self.passed(), '')

    def test_a_failure_with_no_summary_line_prints_the_output_s_end(self):
        head = self.check_pair()
        self.env['STUB_UV'] = 'broken'
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('failed (exit 2)', done.stderr)
        self.assertIn('error: The lockfile at `uv.lock` needs to be updated\n', done.stderr)
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_a_conflict_refuses_and_runs_nothing(self):
        self.commit('base of the conflict', 'server/tests/test_php_claims.py')
        self.git('push', '-q', 'origin', 'HEAD:master')
        head = self.pr('server/tests/test_php_claims.py', 'server/docs/adr/check-paths.sh')
        self.master('server/tests/test_php_claims.py')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('not merging #7; it conflicts with origin/master', done.stderr)
        self.assertIn('Merge origin/master into the branch, push, and wait for CI again. '
                      'The conflicts:\n  server/tests/test_php_claims.py\n', done.stderr)
        self.assertEqual(self.ran(), '')
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_with_no_uv_it_fails_saying_so(self):
        """The source checks need uv (#311's Risks)."""
        head = self.check_pair()
        path = [d for d in os.environ['PATH'].split(':') if d and not (Path(d) / 'uv').exists()]
        if not shutil.which('git', path=':'.join(path)):
            self.skipTest('git sits beside uv')
        (self.tmp / 'stubs' / 'uv').unlink()
        self.env['PATH'] = ':'.join([str(self.tmp / 'stubs'), *path])
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('not merging #7; uv is not on PATH, and server/tools/source-checks.sh '
                      'needs it', done.stderr)
        self.assertEqual(self.ran(), '')
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_a_merge_with_no_source_checks_script_refuses(self):
        head = self.pr('server/docs/adr/check-paths.sh')
        self.git('rm', '-q', 'server/tools/source-checks.sh')
        self.git('commit', '-q', '-m', 'no script')
        head = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'HEAD:refs/pull/7/head')
        self.master('server/tests/test_php_claims.py')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('has no server/tools/source-checks.sh to run', done.stderr)
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_a_worktree_it_cannot_make_refuses(self):
        head = self.check_pair()
        self.cache.write_text('a file where the cache folder goes\n')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('not merging #7; could not make a worktree of the PR merged with '
                      f'origin/master under {self.cache}', done.stderr)
        self.assertEqual(self.ran(), '')
        self.assertEqual(self.merged(), '')
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_passing_checks_go_on_to_the_area_rule(self):
        head = self.pr('plan/tests/test_lint_issues.py')
        self.master('plan/bin/pr-wait')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('the source checks passed on #7', done.stderr)
        self.assertIn('in an area the PR also changes', done.stderr.split('the source checks passed')[1])
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_a_pair_that_passed_is_not_run_again(self):
        """Nor with --dry-run or --base-checked; the area rule's
        --base-checked rerun would otherwise pay for the run twice."""
        head = self.pr('plan/tests/test_lint_issues.py')
        self.master('plan/bin/pr-wait')
        self.merge(head)
        self.assertEqual(self.ran().count('\nuv '), 1, self.ran())
        for argv in ((), ('--dry-run',), ('--base-checked',)):
            with self.subTest(argv=argv):
                done = self.merge(head, *argv)
                self.assertIn('not running them again', done.stderr)
                self.assertEqual(self.ran().count('\nuv '), 1, self.ran())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_a_new_head_or_a_moved_master_runs_them_again(self):
        head = self.check_pair()
        self.merge(head)
        self.assertEqual(self.ran().count('\nuv '), 1)
        head = self.pr('server/docs/adr/check-paths.sh', 'server/ui/kiosk.css')
        done = self.merge(head)
        self.assertNotIn('not running them again', done.stderr)
        self.assertEqual(self.ran().count('\nuv '), 2)
        self.master('docs/install-docker.md')
        done = self.merge(head)
        self.assertNotIn('not running them again', done.stderr)
        self.assertEqual(self.ran().count('\nuv '), 3)
        self.assertEqual(len(self.passed().splitlines()), 3)

    def test_a_failed_run_is_run_again(self):
        head = self.check_pair()
        self.env['STUB_UV'] = 'fail'
        self.merge(head)
        del self.env['STUB_UV']
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.ran().count('\nuv '), 2)

    def test_dry_run_checks_and_merges_nothing(self):
        head = self.check_pair()
        done = self.merge(head, '--dry-run')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f'dry run: #7 would merge at {head}\n', done.stderr)
        self.assertIn('uv ', self.ran())
        self.assertEqual(self.merged(), '')

    def test_dry_run_refuses_as_the_merge_would(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.master('plan/bin/affected-tests')
        done = self.merge(head, '--dry-run')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertNotIn('dry run', done.stderr)

    def test_help_says_which_rule_base_checked_skips(self):
        done = subprocess.run(['bash', str(PR_MERGE), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('--base-checked  skips the area rule', done.stdout)
        self.assertIn('the source-check run still runs', done.stdout)
        self.assertIn('pr-merge-source-checks', done.stdout)
        self.assertIn('16 min', done.stdout)

    def hang(self, sig, group, stub='hang'):
        """Starts a merge whose checks hang, writing into the worktree, and
        sends `sig` once they run: to the process group as Ctrl-C does, or
        to pr-merge alone."""
        head = self.check_pair()
        self.env['STUB_UV'] = stub
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, head=head, log=str(self.log)))
        gh.chmod(0o755)
        # As at a terminal, SIGINT is not ignored: bash can't trap a signal
        # ignored when it starts, and a background job's is.
        proc = subprocess.Popen(['bash', str(PR_MERGE), '7'], cwd=self.work,
                                env={**self.env, 'GH': str(gh)}, start_new_session=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        pid_file = Path(f'{self.ran_log}.pid')
        deadline = time.monotonic() + 120
        while not pid_file.exists() or not pid_file.read_text().strip():
            self.assertIsNone(proc.poll(), proc.stderr.read() if proc.poll() is not None else '')
            self.assertLess(time.monotonic(), deadline, 'the stub never started')
            time.sleep(0.1)
        stub = int(pid_file.read_text())
        # The run has a session of its own, so a test that fails leaves it
        # running unless this stops it.
        group_of_stub = os.getpgid(stub)
        self.addCleanup(lambda: subprocess.run(['kill', '-KILL', '--', f'-{group_of_stub}'],
                                               capture_output=True))
        if group:
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
        try:
            out, err = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
        return proc.returncode, err, stub

    def assert_gone(self, pid):
        deadline = time.monotonic() + 30
        while True:
            try:
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                return
            if state == 'Z':
                return
            self.assertLess(time.monotonic(), deadline, f'{pid} still runs ({state})')
            time.sleep(0.1)

    def test_ctrl_c_stops_the_checks_and_removes_the_worktree(self):
        status, err, stub = self.hang(signal.SIGINT, group=True)
        self.assertEqual(status, 130, err)
        self.assert_gone(stub)
        self.assert_no_worktree()
        self.assertEqual(self.merged(), '')
        self.assertEqual(self.passed(), '')

    def test_a_run_that_ignores_term_is_killed(self):
        status, err, stub = self.hang(signal.SIGINT, group=True, stub='deaf')
        self.assertEqual(status, 130, err)
        self.assert_gone(stub)
        self.assert_no_worktree()

    def test_a_term_stops_the_checks_and_removes_the_worktree(self):
        status, err, stub = self.hang(signal.SIGTERM, group=False)
        self.assertEqual(status, 143, err)
        self.assert_gone(stub)
        self.assert_no_worktree()
        self.assertEqual(self.merged(), '')

    def test_a_hang_up_stops_the_checks_and_removes_the_worktree(self):
        status, err, stub = self.hang(signal.SIGHUP, group=False)
        self.assertEqual(status, 129, err)
        self.assert_gone(stub)
        self.assert_no_worktree()
        self.assertEqual(self.merged(), '')

    @unittest.skipUnless(shutil.which('timeout'), 'no timeout')
    def test_under_timeout_the_run_is_refused(self):
        """source-checks.sh refuses to run under `timeout`, and pr-merge
        under it inherits that (its help says so)."""
        head = self.check_pair()
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, head=head, log=str(self.log)))
        gh.chmod(0o755)
        done = subprocess.run(['timeout', '300', 'bash', str(PR_MERGE), '7'], cwd=self.work,
                              env={**self.env, 'GH': str(gh)}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('server/tools/source-checks.sh failed (exit 2)', done.stderr)
        self.assertIn('source-checks.sh: it runs under timeout', done.stderr)
        self.assertEqual(self.ran(), '')
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_base_checked_with_a_failed_fetch_refuses(self):
        """The source-check rule reads both sides, so --base-checked can't
        stand in for the fetch any more (#311)."""
        head = self.pr('plan/AGENT-BRIEF.md')
        self.git('remote', 'set-url', 'origin', str(self.tmp / 'gone.git'))
        done = self.merge(head, '--base-checked')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('could not fetch origin/master and its head', done.stderr)
        self.assertNotIn('--base-checked', done.stderr)
        self.assertEqual(self.merged(), '')


if __name__ == '__main__':
    unittest.main()
