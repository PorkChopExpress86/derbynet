"""plan/bin/pr-overlap against a fake gh and a scratch repository with a bare
origin whose refs/pull/<n>/head hold each PR's head, so it runs with no
network.

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

PR_OVERLAP = Path(__file__).resolve().parent.parent / 'bin' / 'pr-overlap'

# Answers `pr list` with the PRs in PRS, or fails when FAIL is set.
FAKE_GH = '''#!{python}
import sys
if {fail!r}:
    sys.exit('gh: could not resolve to a Repository')
if sys.argv[1:3] != ['pr', 'list']:
    sys.exit('pr-overlap ran gh ' + ' '.join(sys.argv[1:]))
print({prs!r})
'''

LINES = ''.join(f'line {n}\n' for n in range(1, 6))


@unittest.skipUnless(shutil.which('git'), 'the PR heads are git commits')
class PrOverlap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        # A test's git calls drop every GIT_* var (memory git-dir-in-hooks).
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                        GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1')
        self.origin = self.tmp / 'origin.git'
        self.clone = self.tmp / 'clone'
        self.git(self.tmp, 'init', '-q', '--bare', '-b', 'master', str(self.origin))
        self.git(self.tmp, 'clone', '-q', str(self.origin), str(self.clone))
        self.git(self.clone, 'checkout', '-q', '-b', 'master')
        for name in ('a.txt', 'b.txt', 'e.txt'):
            (self.clone / name).write_text(LINES)
        self.git(self.clone, 'add', '.')
        self.git(self.clone, 'commit', '-qm', 'base')
        self.git(self.clone, 'push', '-q', 'origin', 'master')
        self.base = self.git(self.clone, 'rev-parse', 'HEAD')
        self.prs = {}
        # The tool's env: no GIT_* var and no git identity, as in a fresh
        # clone. useConfigOnly stops git guessing one from the host name.
        home = self.tmp / 'home'
        home.mkdir()
        (home / '.gitconfig').write_text('[user]\n\tuseConfigOnly = true\n')
        self.tool_env = {k: v for k, v in self.env.items() if not k.startswith('GIT_')}
        self.tool_env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'xdg'))

    def git(self, cwd, *args):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def pr(self, number, edits):
        """A PR whose head, on master's tip, sets the given line of each
        file in `edits` to its text."""
        self.git(self.clone, 'checkout', '-q', '--detach', 'origin/master')
        for name, (line, text) in edits.items():
            lines = (self.clone / name).read_text().splitlines(keepends=True)
            lines[line - 1] = text + '\n'
            (self.clone / name).write_text(''.join(lines))
        self.git(self.clone, 'commit', '-qam', f'pr {number}')
        head = self.git(self.clone, 'rev-parse', 'HEAD')
        self.git(self.clone, 'push', '-q', 'origin', f'HEAD:refs/pull/{number}/head')
        self.prs[number] = {'number': number, 'headRefName': f'issue-{number}',
                            'headRefOid': head,
                            'files': [{'path': name} for name in edits]}

    def master_moves(self, edits):
        self.git(self.clone, 'checkout', '-q', '--detach', 'origin/master')
        for name, (line, text) in edits.items():
            lines = (self.clone / name).read_text().splitlines(keepends=True)
            lines[line - 1] = text + '\n'
            (self.clone / name).write_text(''.join(lines))
        self.git(self.clone, 'commit', '-qam', 'master moves')
        self.git(self.clone, 'push', '-q', 'origin', 'HEAD:master')

    def overlap(self, number, fail=False, **env):
        gh = self.tmp / 'gh'
        gh.write_text(FAKE_GH.format(python=sys.executable, fail=fail,
                                     prs=json.dumps(list(self.prs.values()))))
        gh.chmod(0o755)
        return subprocess.run([sys.executable, '-I', str(PR_OVERLAP), str(number)],
                              cwd=self.clone, env={**self.tool_env, 'GH': str(gh), **env},
                              capture_output=True, text=True, timeout=60)

    def test_two_prs_editing_one_line_conflict(self):
        """#424 and #433 each built `STOP_SENDING` in the browser conftest
        (#347 retro)."""
        self.pr(424, {'a.txt': (1, 'stub 424'), 'e.txt': (1, 'e 424')})
        self.pr(433, {'a.txt': (1, 'stub 433'), 'e.txt': (5, 'e 433')})
        run = self.overlap(433)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        head = self.prs[424]['headRefOid'][:8]
        self.assertEqual(run.stdout.splitlines(), [
            f"#424 issue-424 {head}: changes 2 of #433's files;"
            ' git cannot merge the two (1 conflicts)',
            '  conflict  a.txt',
            '  both      e.txt',
        ])

    def test_two_prs_editing_one_file_apart_merge(self):
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        run = self.overlap(2)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(run.stdout.splitlines()[1:], ['  both      a.txt'])
        self.assertTrue(run.stdout.splitlines()[0].endswith('; git merges the two'),
                        run.stdout)

    def test_no_shared_file_exits_0(self):
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'b.txt': (1, 'two')})
        run = self.overlap(2)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout, "no other open PR changes a file #2 changes\n")

    def test_a_stale_head_is_merged_with_master_first(self):
        """#393's head, behind master, read as conflicting with #424 in two
        files neither changed: the conflict was #393's with master. Each head
        is merged with master first, so the pair is compared on master."""
        self.pr(393, {'b.txt': (1, 'b 393'), 'e.txt': (5, 'e 393')})
        self.master_moves({'b.txt': (1, 'b master')})
        self.pr(424, {'e.txt': (1, 'e 424')})
        run = self.overlap(424)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(run.stdout.splitlines()[0].partition(': ')[2],
                         "changes 1 of #424's files; not merged,"
                         ' since #393 conflicts with origin/master')
        self.assertNotIn('conflict  ', run.stdout)
        self.assertNotIn('b.txt', run.stdout)

    def test_a_head_that_conflicts_with_master_is_named(self):
        self.pr(5, {'b.txt': (1, 'b 5'), 'a.txt': (1, 'a 5')})
        self.master_moves({'b.txt': (1, 'b master')})
        self.pr(6, {'a.txt': (5, 'a 6')})
        run = self.overlap(5)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        head = self.prs[6]['headRefOid'][:8]
        self.assertEqual(run.stdout.splitlines(), [
            '#5 conflicts with origin/master; update it, then rerun',
            f"#6 issue-6 {head}: changes 1 of #5's files;"
            ' not merged, since #5 conflicts with origin/master',
            '  both      a.txt',
        ])

    def test_both_heads_that_conflict_with_master_are_named(self):
        self.pr(5, {'b.txt': (1, 'b 5'), 'a.txt': (1, 'a 5')})
        self.pr(6, {'b.txt': (1, 'b 6'), 'a.txt': (5, 'a 6')})
        self.master_moves({'b.txt': (1, 'b master')})
        run = self.overlap(5)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('; not merged, since #5 and #6 conflict with origin/master',
                      run.stdout.splitlines()[1])

    def test_git_vars_in_the_environment_are_dropped(self):
        """Under a hook, GIT_DIR names the hook's repository; the tool works
        in the repository it runs in."""
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        run = self.overlap(2, GIT_DIR=str(self.tmp / 'nonexistent'),
                           GIT_WORK_TREE=str(self.tmp / 'nonexistent'))
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertTrue(run.stdout.splitlines()[0].endswith('; git merges the two'),
                        run.stdout)

    def test_the_rest_of_the_environment_is_kept(self):
        """Only GIT_* goes: git still reads the HOME config, here an
        insteadOf that gives the clone's remote its address."""
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        self.git(self.clone, 'remote', 'set-url', 'origin', 'alias:origin')
        with (Path(self.tool_env['HOME']) / '.gitconfig').open('a') as config:
            config.write(f'[url "{self.origin}"]\n\tinsteadOf = alias:origin\n')
        run = self.overlap(2)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertTrue(run.stdout.splitlines()[0].endswith('; git merges the two'),
                        run.stdout)

    def test_a_clone_with_no_git_identity_merges_the_heads(self):
        """The merge commits need an author, which a fresh clone has not
        set."""
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        probe = subprocess.run(['git', 'commit-tree', '-m', 'x', 'HEAD^{tree}'],
                               cwd=self.clone, env=self.tool_env, capture_output=True,
                               text=True)
        self.assertNotEqual(probe.returncode, 0, 'the env must lack an identity')
        run = self.overlap(2)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertTrue(run.stdout.splitlines()[0].endswith('; git merges the two'),
                        run.stdout)

    def test_a_head_merged_cleanly_with_master_still_conflicts_with_the_other(self):
        """A PR behind master that merges with it cleanly is compared, not
        skipped."""
        self.pr(7, {'a.txt': (1, 'a 7')})
        self.master_moves({'b.txt': (1, 'b master')})
        self.pr(8, {'a.txt': (1, 'a 8')})
        run = self.overlap(8)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('git cannot merge the two (1 conflicts)', run.stdout)
        self.assertIn('  conflict  a.txt', run.stdout)

    def test_a_pr_that_is_not_open_exits_2(self):
        self.pr(1, {'a.txt': (1, 'one')})
        run = self.overlap(9)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn('#9 is not an open PR', run.stderr)

    def test_a_failed_gh_exits_2(self):
        self.pr(1, {'a.txt': (1, 'one')})
        run = self.overlap(1, fail=True)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn('gh pr list: gh: could not resolve', run.stderr)

    def test_a_head_origin_lacks_exits_2(self):
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        self.git(self.clone, 'push', '-q', 'origin', ':refs/pull/2/head')
        run = self.overlap(1)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn('pr-overlap: git fetch', run.stderr)

    def test_a_head_git_cannot_merge_exits_2(self):
        """A `headRefOid` that names no commit (here a file's blob) fails
        git merge-tree, which is no clean merge."""
        self.pr(1, {'a.txt': (1, 'one')})
        self.pr(2, {'a.txt': (5, 'two')})
        self.prs[2]['headRefOid'] = self.git(self.clone, 'rev-parse', 'HEAD:a.txt')
        run = self.overlap(1)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn('pr-overlap: git merge-tree', run.stderr)

    def test_no_argument_prints_the_usage_and_exits_2(self):
        run = subprocess.run([sys.executable, '-I', str(PR_OVERLAP)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn('Usage: plan/bin/pr-overlap <pr>', run.stderr)

    def test_help_prints_the_usage_and_exits_0(self):
        for flag in ('-h', '--help'):
            with self.subTest(flag):
                run = subprocess.run([sys.executable, '-I', str(PR_OVERLAP), flag],
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0)
                self.assertIn('Usage: plan/bin/pr-overlap <pr>', run.stdout)


if __name__ == '__main__':
    unittest.main()
