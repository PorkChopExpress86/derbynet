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

import k_patterns  # noqa: F401  a -k that matches no test is refused

PR_MERGE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-merge'
SOURCE_CHECKS = Path(__file__).resolve().parents[2] / 'server' / 'tools' / 'source-checks.sh'

# Stand-ins for the two halves of server/tools/source-checks.sh, so no real
# check runs (as server/tests/test_source_checks.py stubs them): each logs
# where it ran, the merge's parents and files, then prints what the real tool
# prints and exits as $STUB_<NAME> says (pass, fail, broken, hang, deaf:
# hang and ignore SIGTERM, move: push $STUB_PUSH_FROM's HEAD to master
# once, or cut: move $STUB_ORIGIN away). A hang stops after $STUB_LIMIT seconds (120), so a test run that is
# killed leaves no stub looping (#333 review).
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
    while [ "$SECONDS" -lt "${{STUB_LIMIT:-120}}" ]; do date >>busy; sleep 0.1; done ;;
  uv:deaf)
    trap '' TERM
    echo $$ >"$RAN.pid"
    while [ "$SECONDS" -lt "${{STUB_LIMIT:-120}}" ]; do date >>busy; sleep 0.1; done ;;
  uv:cut)
    # origin goes away while the checks run.
    [ -e "$STUB_ORIGIN" ] && mv "$STUB_ORIGIN" "$STUB_ORIGIN.gone" ;;
  uv:move)
    # Another PR merges while the checks run.
    [ -e "$RAN.moved" ] || {{ git -C "$STUB_PUSH_FROM" push -q origin HEAD:master && touch "$RAN.moved"; }} ;;
esac
'''

# Every check passed (the --jq filter prints nothing) and the PR closes
# nothing, so only the base check can refuse. `pr merge` is logged.
FAKE_GH = '''#!{python}
import os, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view'] and 'headRefOid' in args:
    print({head!r})
elif args[:2] == ['pr', 'view'] and 'closingIssuesReferences' in args:
    print(os.environ.get('FAKE_CLOSES', ''))
elif args[:2] == ['pr', 'view'] and 'body' in args:
    if os.environ.get('FAKE_BODY_FAILS'):
        sys.exit(1)
    print(os.environ.get('FAKE_BODY', ''))
elif args[:2] == ['issue', 'view'] and 'state,body' in args:
    print('## Still open\\n\\n- the screenshots')
elif args[:2] == ['pr', 'view'] and 'state,mergeCommit' in args:
    print('MERGED abc')
elif args[:1] == ['api'] and os.environ.get('FAKE_REJECTED'):
    # rejected-workflows' reads, after their --jq: run 44236545824 on #59's
    # ff00bbc6 failed with no job.
    print('0' if args[1].endswith('/jobs') else
          '44236545824 .github/workflows/pi-images.yml https://example/44236545824')
elif args[:2] == ['pr', 'merge']:
    if os.environ.get('FAKE_MERGE_HANG'):
        open(os.environ['FAKE_MERGE_HANG'], 'w').close()
        import time
        time.sleep(30)
    with open({log!r}, 'a') as f:
        f.write(' '.join(args) + '\\n')
'''


def setUpModule():
    """These tests run the real source-checks.sh, which refuses a `timeout`
    above it, so under one they fail on that refusal: #311's timing run
    under `timeout 300` failed 9 subtests, and its retro's mutant run failed
    on it too (#311 retro). Fail once, before any test, saying so."""
    pid = os.getppid()
    while pid > 1:
        try:
            comm = Path(f'/proc/{pid}/comm').read_text().strip()
            pid = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return
        if comm == 'timeout':
            raise RuntimeError(
                'test_pr_merge.py runs under timeout, which source-checks.sh refuses: '
                'run it with no timeout')


class PrMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                        GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1', CLOSE_WAIT='0',
                        # A deaf run's 10 s waits took 21 s of plan/tests' 74 s (#311 retro).
                        KILL_WAIT='3')
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

    def decisions(self, *ids):
        """plan/owner-decisions.md text with a row for each of `ids`."""
        rows = ''.join(f'| {d} | 2026-11-18 | a decision | #1 |\n' for d in ids)
        return '| ID | Date | Decision | Recorded in |\n|---|---|---|---|\n' + rows

    def write(self, rel, text):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_a_decision_the_body_cites_with_no_row_refuses(self):
        """#386's body cited D133 in prose while #420, which adds its row,
        was still open, and #386 merged first (#378 retro)."""
        self.git('switch', '-q', '-C', 'moved', 'origin/master')
        self.write('plan/owner-decisions.md', self.decisions('D101'))
        self.commit('D101 recorded')
        self.git('push', '-q', 'origin', 'HEAD:master')
        self.git('switch', '-q', 'branch')
        head = self.pr('server/ui/kiosk.css')
        self.env['FAKE_BODY'] = ("the owner narrowed D101's repeats for `test_kiosk*.py`\r\n"
                                 'files to one font under load (D133), and D1330 too.\r\n')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f'not merging #7; its body cites D133 D1330, which plan/owner-decisions.md '
                      f'has no row for on origin/master {self.git("rev-parse", "origin/master")[:8]} '
                      f'or on its head {head[:8]}', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_a_decision_recorded_on_master_merges(self):
        self.git('switch', '-q', '-C', 'moved', 'origin/master')
        self.write('plan/owner-decisions.md', self.decisions('D101', 'D133'))
        self.commit('D101 and D133 recorded')
        self.git('push', '-q', 'origin', 'HEAD:master')
        self.git('switch', '-q', 'branch')
        head = self.pr('server/ui/kiosk.css')
        self.env['FAKE_BODY'] = 'D101 as amended by D133.'
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_a_decision_the_pr_records_merges(self):
        """#420 cites D133 and adds its row."""
        self.write('plan/owner-decisions.md', self.decisions('D133'))
        head = self.pr('server/ui/kiosk.css')
        self.env['FAKE_BODY'] = 'D133 narrows the kiosk repeats to one font.'
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_a_quoted_decision_is_not_cited(self):
        """A body that only quotes an id, in a code span or a fence, cites
        nothing; neither does an id inside a longer word."""
        head = self.pr('server/ui/kiosk.css')
        self.env['FAKE_BODY'] = 'the row `D133` is #420\'s\n\n```\nD134 in a log\n```\nAD5 X_D6\n'
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('pr merge 7', self.merged())

    def test_a_body_it_cannot_read_refuses(self):
        head = self.pr('server/ui/kiosk.css')
        self.env['FAKE_BODY_FAILS'] = '1'
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('not merging #7; could not read its body', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_a_workflow_github_rejected_refuses(self):
        """#59's pi-images.yml showed no check on the PR, and every listed
        check had passed (#59 retro)."""
        head = self.pr('plan/AGENT-BRIEF.md')
        self.env['FAKE_REJECTED'] = '1'
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f'not merging #7; GitHub rejected a workflow file on {head}', done.stderr)
        self.assertIn('rejected .github/workflows/pi-images.yml https://example/44236545824',
                      done.stderr)
        self.assertEqual(self.merged(), '')
        self.assertEqual(self.ran(), '')

    def test_a_run_list_gh_cannot_read_refuses(self):
        head = self.pr('plan/AGENT-BRIEF.md')
        self.env.update(FAKE_REJECTED='1', PR_WAIT_RETRY_SLEEP='0')
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, head=head, log=str(self.log))
                      .replace("os.environ.get('FAKE_REJECTED'):",
                               "os.environ.get('FAKE_REJECTED'):\n    sys.exit(1)"))
        gh.chmod(0o755)
        done = subprocess.run(['bash', str(PR_MERGE), '7'], cwd=self.work,
                              env={**self.env, 'GH': str(gh)}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(f'not merging #7; could not read the workflow runs on {head}', done.stderr)
        self.assertEqual(self.merged(), '')

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

    def test_the_run_prints_its_own_time_and_load(self):
        """#330's and #335's merges printed "it took 16 min at a load
        average of 34", #333's time, as if it were the run they started
        (#311 retro)."""
        took = r' (in|after) \d+ s, with a load average of \d+\.\d+ at the end'
        head = self.check_pair()
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("output in ", done.stderr)
        self.assertIn("; #333's run took 16 min at a load average of 34\n", done.stderr)
        self.assertNotIn('; it took', done.stderr)
        self.assertRegex(done.stderr, r'the source checks passed on #7 merged with origin/master '
                         r'[0-9a-f]{8}' + took + '\n')
        # A new head, so the passed pair doesn't skip the run.
        head = self.pr('server/tools/other.sh')
        self.env['STUB_UV'] = 'fail'
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertRegex(done.stderr, r'source-checks.sh failed \(exit 1\) on the PR merged with '
                         r'origin/master [0-9a-f]{8},' + took + r'\. Its summary lines')

    def test_a_failing_check_refuses_and_prints_its_summary_lines(self):
        """Each half's failures, in the formats unittest and pytest -q print
        them; the rest of the output stays in the log."""
        head = self.check_pair()
        self.env.update(STUB_PYTHON3='fail', STUB_UV='fail')
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.merged(), '')
        (log,) = self.cache.glob('derbynet-pr-merge-7-*.log')
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
                self.assertNotIn('16 min', done.stderr)
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

    def hang(self, sig, group, stub='hang', twice=False):
        """Starts a merge whose checks hang, writing into the worktree, and
        sends `sig` once they run: to the process group as Ctrl-C does, or
        to pr-merge alone; with `twice`, again a second later."""
        head = self.check_pair()
        self.env['STUB_UV'] = stub
        proc, stub = self.start(head)
        if group:
            os.killpg(proc.pid, sig)
        else:
            proc.send_signal(sig)
        if twice:
            time.sleep(1)
            if proc.poll() is None:
                os.killpg(proc.pid, sig) if group else proc.send_signal(sig)
        try:
            out, err = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
        return proc.returncode, err, stub

    def start(self, head):
        """Starts pr-merge 7 and returns it and the hung stub's pid once the
        stub runs."""
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
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))
        return proc, stub

    def alive(self, pid):
        try:
            state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            return False
        return state != 'Z'

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

    def ran_count(self, name='uv'):
        return sum(line.startswith(f'{name} ') for line in self.ran().splitlines())

    def test_master_moved_during_the_run_refuses(self):
        """Another PR merged while the checks ran, so they never saw the
        master the merge goes onto (#333 review): it refuses, and the rerun
        checks the new pair."""
        head = self.check_pair()
        old = self.git('rev-parse', 'origin/master')
        other = self.tmp / 'other'
        self.git('clone', '-q', str(self.tmp / 'origin.git'), str(other), cwd=self.tmp)
        (other / 'server' / 'tests' / 'test_pipefail_pipes.py').write_text('#304\n')
        self.git('add', '.', cwd=other)
        self.git('commit', '-q', '-m', '#304: a source check, merged mid-run', cwd=other)
        new = self.git('rev-parse', 'HEAD', cwd=other)
        self.env.update(STUB_UV='move', STUB_PUSH_FROM=str(other))
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('the source checks passed on #7', done.stderr)
        self.assertIn(f'not merging #7; origin/master moved during the run '
                      f'({old[:8]}..{new[:8]}); rerun pr-merge\n', done.stderr)
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()
        del self.env['STUB_UV']
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('not running them again', done.stderr)
        self.assertEqual(self.ran_count(), 2)
        self.assertIn(f'{head} {new}\n', self.passed())
        self.assertIn('pr merge 7', self.merged())

    def test_a_dry_run_also_refuses_a_master_that_moved(self):
        head = self.check_pair()
        other = self.tmp / 'other'
        self.git('clone', '-q', str(self.tmp / 'origin.git'), str(other), cwd=self.tmp)
        (other / 'docs').mkdir()
        (other / 'docs' / 'x.md').write_text('x\n')
        self.git('add', '.', cwd=other)
        self.git('commit', '-q', '-m', 'moved', cwd=other)
        self.env.update(STUB_UV='move', STUB_PUSH_FROM=str(other))
        done = self.merge(head, '--dry-run')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('origin/master moved during the run', done.stderr)
        self.assertNotIn('dry run:', done.stderr)

    def test_a_second_ctrl_c_during_cleanup_still_removes_the_worktree(self):
        """The first Ctrl-C starts the cleanup, which waits out a run that
        ignores TERM; a second Ctrl-C used to end pr-merge there and leave
        both (#333 review)."""
        status, err, stub = self.hang(signal.SIGINT, group=True, stub='deaf', twice=True)
        self.assertEqual(status, 130, err)
        self.assert_gone(stub)
        self.assert_no_worktree()

    def test_a_killed_pr_merge_s_run_is_cleaned_up_by_the_next(self):
        """SIGKILL runs no trap: the run and its worktree stay until the next
        pr-merge finds its owner gone (#333 review)."""
        head = self.check_pair()
        self.env['STUB_UV'] = 'hang'
        proc, stub = self.start(head)
        os.kill(proc.pid, signal.SIGKILL)
        proc.communicate(timeout=60)
        self.assertTrue(self.alive(stub))
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 2)
        del self.env['STUB_UV']
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertRegex(done.stderr, r'removed .*/derbynet-pr-merge\.\w+, left by pr-merge '
                                      r'\d+, which is gone\n')
        self.assert_gone(stub)
        self.assert_no_worktree()

    def test_a_live_pr_merge_s_worktree_is_left_alone(self):
        """A worktree whose owner still runs is another pr-merge's run."""
        self.cache.mkdir()
        left = self.cache / 'derbynet-pr-merge.live01'
        self.git('worktree', 'add', '-q', '--detach', str(left), 'HEAD')
        owner = subprocess.Popen(['sleep', '120'])
        self.addCleanup(lambda: (owner.kill(), owner.wait()))
        gitdir = Path(self.git('-C', str(left), 'rev-parse', '--absolute-git-dir'))
        (gitdir / 'pr-merge-owner').write_text(f'owner {owner.pid}\n')
        head = self.pr('plan/AGENT-BRIEF.md')
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('removed', done.stderr)
        self.assertTrue(left.is_dir())
        owner.kill()
        owner.wait()
        done = self.merge(head)
        self.assertIn(f'removed {left}, left by pr-merge {owner.pid}, which is gone', done.stderr)
        self.assertFalse(left.exists())
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_a_hung_stub_stops_by_itself(self):
        """So a test run that is killed leaves no stub writing every 0.1 s
        (#333 review)."""
        stub = self.tmp / 'stubs' / 'uv'
        done = subprocess.run([str(stub)], cwd=self.work, capture_output=True, text=True,
                              env={**self.env, 'STUB_UV': 'hang', 'STUB_LIMIT': '1'},
                              timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_each_run_keeps_its_own_log(self):
        """A passing run deleted a failed one's log, which both named
        derbynet-pr-merge-7.log (#333 review)."""
        head = self.check_pair()
        self.env['STUB_UV'] = 'fail'
        logs = []
        for _ in range(2):
            done = self.merge(head)
            logs.append(Path(re.search(r'whole output in (\S+):\n', done.stderr).group(1)))
        self.assertNotEqual(logs[0], logs[1])
        del self.env['STUB_UV']
        done = self.merge(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for log in logs:
            self.assertTrue(log.is_file(), log)
            self.assertTrue(log.name.startswith('derbynet-pr-merge-7-'), log)

    def test_an_env_without_default_signal_refuses_naming_env(self):
        """With an env that has no --default-signal, the run's exit 125 read
        as a failed source check (#333 review)."""
        old = self.tmp / 'oldenv'
        old.mkdir()
        (old / 'env').write_text('#!/bin/bash\n'
                                 'for a; do case $a in --default-signal*) '
                                 'echo "env: unrecognized option \'$a\'" >&2; exit 125;; esac; done\n'
                                 'exec /usr/bin/env "$@"\n')
        (old / 'env').chmod(0o755)
        self.env['PATH'] = f'{old}:{self.env["PATH"]}'
        head = self.check_pair()
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('not merging #7; env takes no --default-signal', done.stderr)
        self.assertNotIn('source-checks.sh failed', done.stderr)
        self.assertEqual(self.ran(), '')
        self.assertEqual(self.merged(), '')
        self.assert_no_worktree()

    def test_dry_run_stops_at_an_issue_still_open(self):
        """--dry-run runs every gate, the `## Still open` one too (#333
        review: a dry run that stopped above it passed every test)."""
        head = self.pr('plan/AGENT-BRIEF.md')
        self.env['FAKE_CLOSES'] = '12'
        done = self.merge(head, '--dry-run')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('it closes #12, whose body has a "## Still open" section', done.stderr)
        self.assertNotIn('dry run:', done.stderr)

    def test_a_master_it_cannot_fetch_again_refuses(self):
        head = self.check_pair()
        self.env.update(STUB_UV='cut', STUB_ORIGIN=str(self.tmp / 'origin.git'))
        done = self.merge(head)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('the source checks passed on #7', done.stderr)
        self.assertIn('not merging #7; could not fetch origin/master again to see whether it '
                      'moved during the run; rerun pr-merge\n', done.stderr)
        self.assertEqual(self.merged(), '')

    def test_the_traps_are_back_after_the_run(self):
        """The cleanup ignores INT, HUP and TERM while it runs, then sets
        their traps again: a TERM while gh merges still stops pr-merge, and
        gh, which would inherit an ignored TERM, with it."""
        head = self.check_pair()
        merging = self.tmp / 'merging'
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, head=head, log=str(self.log)))
        gh.chmod(0o755)
        proc = subprocess.Popen(['bash', str(PR_MERGE), '7'], cwd=self.work,
                                env={**self.env, 'GH': str(gh), 'FAKE_MERGE_HANG': str(merging)},
                                start_new_session=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and os.killpg(proc.pid, signal.SIGKILL))
        deadline = time.monotonic() + 120
        while not merging.exists():
            self.assertIsNone(proc.poll(), proc.stderr.read() if proc.poll() is not None else '')
            self.assertLess(time.monotonic(), deadline, 'gh pr merge never ran')
            time.sleep(0.1)
        start = time.monotonic()
        os.killpg(proc.pid, signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 143, err)
        self.assertEqual(self.merged(), '')
        # The fake gh sleeps 30 s; a TERM it ignored would end pr-merge after that.
        self.assertLess(time.monotonic() - start, 20)


if __name__ == '__main__':
    unittest.main()
