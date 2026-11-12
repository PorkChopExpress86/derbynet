"""plan/bin/pr-merge against a fake gh and a scratch repository with a bare
origin, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PR_MERGE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-merge'

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
        self.work = self.tmp / 'work'
        self.git('init', '-q', '--bare', '-b', 'master', str(self.tmp / 'origin.git'), cwd=self.tmp)
        self.git('clone', '-q', str(self.tmp / 'origin.git'), str(self.work), cwd=self.tmp)
        self.commit('base', 'plan/README.md', 'server/ui/kiosk.css', 'server/conftest.py')
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
        self.git('switch', '-q', '-c', 'moved', 'origin/master')
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


if __name__ == '__main__':
    unittest.main()
