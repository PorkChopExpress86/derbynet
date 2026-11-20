"""plan/bin/commit-closes: a closing keyword in a commit message is refused
before GitHub reads it (#439). The matcher is pr-evidence's, so most cases
call the tool's functions in process (the pre-push hook runs these tests);
a scratch repository and a fake gh hold the command line and `--sweep`.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

BIN = Path(__file__).resolve().parent.parent / 'bin'
COMMIT_CLOSES = BIN / 'commit-closes'


def load_tool():
    """plan/bin/commit-closes as a module."""
    loader = importlib.machinery.SourceFileLoader('commit_closes_tool', str(COMMIT_CLOSES))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL = load_tool()


def refused(message: str, closes=()) -> list[str]:
    return [hit for _sha, _subject, hit, _number, _in_fork
            in TOOL.refusals([('abc', message)], set(closes))]


# #415's retro commit, 7a38fdf5, quotes its keywords in code spans, which the
# PR body's rule lets through.
QUOTED = ('#415 retro: pr-evidence refuses a closing keyword\n\n'
          '#421\'s body quoted a test fixture\'s closing line.\n'
          '- `Closes GH-1` was missed; ``Closes #1`` and "Closes `x` #1" gave wrong results.\n')


class Matcher(unittest.TestCase):
    def test_each_form_pr_evidence_refuses_in_a_body_is_refused_in_a_message(self):
        for words in ('close #59', 'closes #59', 'closed #59', 'fix #59', 'fixes #59',
                      'fixed #59', 'resolve #59', 'resolves #59', 'resolved #59',
                      'CLOSES #59', 'Closes: #59', 'Closes owner/repo#59', 'closes GH-59',
                      'Closes https://github.com/PorkChopExpress86/derbynet/issues/59',
                      '(fixes #59 C4)', 'closes#59', 'closes - #59'):
            with self.subTest(words=words):
                message = f'Subject\n\nBody {words} here.\n'
                self.assertEqual(len(refused(message, {'61'})), 1)
                self.assertEqual(refused(message, {'61'}),
                                 TOOL.MATCHER.foreign_closes(message, {'61'}))

    def test_a_quote_or_a_near_miss_is_let_through(self):
        for words in ('Closes `#59`', '`Closes #59`', '``Closes #59``', 'disclosed #59',
                      'prefixes #59', 'closes the #59 thread', 'see #59', 'Part of #59',
                      'closes 59'):
            with self.subTest(words=words):
                self.assertEqual(refused(f'Subject\n\nBody {words} here.\n'), [])
        self.assertEqual(refused('Subject\n\n```\nCloses #59\n```\n'), [])
        self.assertEqual(refused(QUOTED), [])

    def test_the_issue_the_push_closes_on_purpose_is_let_through(self):
        message = 'Closes #61 and fixes #59.'
        self.assertEqual(refused(message, {'61', '59'}), [])
        self.assertEqual(refused(message, {'61'}), ['line 1: fixes #59'])
        self.assertEqual(refused(message, {'59'}), ['line 1: Closes #61'])

    def test_another_repo_is_refused_though_its_number_is_named(self):
        self.assertEqual(refused('Closes jeffpiazza/derbynet#61', {'61'}),
                         ['line 1: Closes jeffpiazza/derbynet#61'])
        self.assertEqual(refused('Closes PorkChopExpress86/derbynet#61', {'61'}), [])

    def test_each_refusal_names_its_commit_and_subject(self):
        found = [('1' * 40, 'Subject one\n\nFixes #7'), ('2' * 40, 'Clean'),
                 ('3' * 40, 'Subject three\n\nCloses #8, resolves #9')]
        self.assertEqual(
            [(sha[0], subject, hit, number, in_fork)
             for sha, subject, hit, number, in_fork in TOOL.refusals(found, set())],
            [('1', 'Subject one', 'line 3: Fixes #7', '7', True),
             ('3', 'Subject three', 'line 3: Closes #8', '8', True),
             ('3', 'Subject three', 'line 3: resolves #9', '9', True)])

    def test_an_empty_message_is_no_refusal(self):
        self.assertEqual(TOOL.refusals([('a' * 40, ''), ('b' * 40, '\n')], set()), [])


class Repo:
    """A scratch repository: `master`, a remote-tracking origin/master at its
    first commit, and a branch of commits with the given messages."""

    def __init__(self, test: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, self.dir)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                        GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q', '-b', 'master')
        self.n = 0

    def git(self, *argv):
        return subprocess.run(['git', *argv], cwd=self.dir, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, message: str) -> str:
        self.n += 1
        (self.dir / f'f{self.n}').write_text('x\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def tool(self, *argv, env=None):
        return subprocess.run([sys.executable, '-I', str(COMMIT_CLOSES), *argv], cwd=self.dir,
                              env={**self.env, **(env or {})}, capture_output=True, text=True)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.base = self.repo.commit('Upstream: Fixes #1')
        self.repo.git('update-ref', 'refs/remotes/origin/master', self.base)

    def test_the_reproduction_is_refused_and_names_how_to_pass(self):
        """#415's first commit quoted `Closes #399.` unquoted."""
        self.repo.commit('#415 retro: the matcher\n\nCloses #399.')
        done = self.repo.tool()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertRegex(done.stdout, r"closes another issue, \w{8} '#415 retro: the matcher', "
                                      r'line 3: Closes #399')
        self.assertIn('backticks', done.stderr)
        self.assertIn('--closes N', done.stderr)

    def test_the_same_message_in_backticks_passes(self):
        self.repo.commit('#415 retro: the matcher\n\nQuotes `Closes #399.` in its message.')
        done = self.repo.tool()
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, '', ''))

    def test_closes_names_the_issue_the_push_closes_on_purpose(self):
        self.repo.commit('Closes #399, fixes #64.')
        self.assertEqual(self.repo.tool('--closes', '399').returncode, 1)
        self.assertEqual(self.repo.tool('--closes', '399', '--closes', '#64').returncode, 0)

    def test_closes_refuses_what_is_no_issue_number(self):
        for bad in ('abc', '0', '-1', '06', '1.5'):
            with self.subTest(bad=bad):
                done = self.repo.tool(f'--closes={bad}')
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn('takes an issue number', done.stderr)

    def test_the_default_range_is_origin_master_to_head(self):
        """origin/master holds `Fixes #1` and is not read; a keyword between
        clean commits is, once."""
        self.repo.commit('Clean one')
        self.repo.commit('Fixes #2')
        self.repo.commit('Clean three')
        done = self.repo.tool()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout.count('closes another issue'), 1, done.stdout)
        self.assertIn('Fixes #2', done.stdout)
        self.assertNotIn('#1,', done.stdout)

    def test_head_names_the_commit_to_read(self):
        bad = self.repo.commit('Fixes #2')
        self.repo.commit('Clean')
        self.assertEqual(self.repo.tool().returncode, 1)
        self.assertEqual(self.repo.tool('--head', f'{bad}~1').returncode, 0)
        self.assertEqual(self.repo.tool('--head', bad).returncode, 1)

    def test_a_merge_of_origin_master_into_the_branch_reads_only_the_branch(self):
        self.repo.git('checkout', '-q', '-b', 'b')
        self.repo.commit('Branch commit')
        self.repo.git('checkout', '-q', 'master')
        new = self.repo.commit('Master later: Fixes #3')
        self.repo.git('update-ref', 'refs/remotes/origin/master', new)
        self.repo.git('checkout', '-q', 'b')
        self.repo.git('merge', '-q', '--no-edit', 'master')
        done = self.repo.tool()
        self.assertEqual((done.returncode, done.stdout), (0, ''), done.stderr)

    def test_a_base_option_replaces_origin_master(self):
        bad = self.repo.commit('Fixes #2')
        self.repo.commit('Clean')
        self.assertEqual(self.repo.tool().returncode, 1)
        self.assertEqual(self.repo.tool('--base', bad).returncode, 0)
        self.assertEqual(self.repo.tool('--base', f'{bad}~1').returncode, 1)

    def test_with_no_origin_master_each_commit_no_remote_has_is_read(self):
        self.repo.git('update-ref', '-d', 'refs/remotes/origin/master')
        done = self.repo.tool()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('Fixes #1', done.stdout)

    def test_a_revision_that_names_no_commit_exits_2(self):
        for argv in (('--head', 'nope'), ('--base', 'nope')):
            with self.subTest(argv=argv):
                done = self.repo.tool(*argv)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn('names no commit', done.stderr)

    def test_a_revision_git_reads_as_an_option_reaches_no_git_log(self):
        target = self.repo.dir / 'written'
        done = self.repo.tool('--head', f'--output={target}')
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(target.exists())

    def test_a_clone_that_is_no_repository_exits_2_not_0(self):
        shutil.rmtree(self.repo.dir / '.git')
        done = self.repo.tool()
        self.assertEqual(done.returncode, 2, done.stderr)

    def test_a_history_git_log_cannot_read_exits_2_not_0(self):
        """The head resolves, but its parent's object is gone: an empty log
        must not pass for a clean one."""
        self.repo.commit('Fixes #2')
        parent = self.repo.git('rev-parse', 'HEAD~1')
        (self.repo.dir / '.git' / 'objects' / parent[:2] / parent[2:]).unlink()
        done = self.repo.tool()
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('git log failed', done.stderr)


def fake_gh(tmp: Path, data: dict) -> dict:
    """A `gh` that answers `pr list`, `pr view N` and `api .../issues/N/events`
    from `data` (the events as two pages, as `--slurp` prints them), and the environment that runs the tool with it."""
    (tmp / 'data.json').write_text(json.dumps(data))
    gh = tmp / 'gh'
    gh.write_text(f'''#!{sys.executable}
import json, sys
data = json.load(open({str(tmp / 'data.json')!r}))
a = sys.argv[1:]
if a[:2] == ['pr', 'list']:
    print(json.dumps(data['list']))
elif a[:2] == ['pr', 'view']:
    print(json.dumps(data['pr'][a[2]]))
elif a[0] == 'api' and a[1].endswith('/events'):
    events = data['events'][a[1].split('/')[-2]]
    assert a[2:] == ['--paginate', '--slurp'], a
    print(json.dumps([events[:1], events[1:]]))
else:
    sys.exit('fake gh: ' + ' '.join(a))
''')
    gh.chmod(0o755)
    return {'GH': str(gh), 'REPO': 'PorkChopExpress86/derbynet'}


def pr(number, merged, *messages, closing=(), oid=None):
    return {'number': number, 'mergedAt': merged,
            'mergeCommit': {'oid': oid or f'{number:040x}'},
            'closingIssuesReferences': [{'number': n} for n in closing],
            'commits': [{'oid': f'{number}{i}'.ljust(40, 'a'),
                         'messageHeadline': m.split('\n')[0],
                         'messageBody': '\n'.join(m.split('\n')[1:])}
                        for i, m in enumerate(messages)]}


class Sweep(unittest.TestCase):
    """The oracle: each refusal over the last N merged PRs against what
    GitHub did with the issue, from the PR's merge commit and the issue's
    events."""

    def run_sweep(self, count, prs, events):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        data = {'list': [{'number': p['number'], 'mergedAt': p['mergedAt']} for p in prs],
                'pr': {str(p['number']): p for p in prs}, 'events': events}
        return subprocess.run([sys.executable, '-I', str(COMMIT_CLOSES), '--sweep', str(count)],
                              capture_output=True, text=True,
                              env={**os.environ, **fake_gh(tmp, data)})

    def test_each_verdict_is_told_and_only_an_issue_left_open_fails(self):
        prs = [pr(10, '2026-11-10T00:00:00Z', 'Fixes #5', oid='a' * 40),
               pr(11, '2026-11-11T00:00:00Z', 'Fixes #6'),
               pr(12, '2026-11-12T00:00:00Z', 'Fixes #7', closing=[7]),
               pr(13, '2026-11-13T00:00:00Z', 'Clean commit'),
               pr(14, '2026-11-14T00:00:00Z', 'Fixes jeffpiazza/derbynet#8')]
        events = {'5': [{'event': 'referenced', 'commit_id': 'c' * 40, 'created_at': '2026-11-09T00:00:00Z'},
                        {'event': 'closed', 'commit_id': 'a' * 40, 'created_at': '2026-11-10T00:00:05Z'}],
                  '6': [{'event': 'closed', 'commit_id': None, 'created_at': '2026-11-01T00:00:00Z'}],
                  '7': [{'event': 'closed', 'commit_id': None, 'created_at': '2026-11-12T00:00:02Z'}]}
        done = self.run_sweep(5, prs, events)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [
            'PR #10 100aaaaa line 1: Fixes #5: GitHub closed it with the merge commit',
            'PR #11 110aaaaa line 1: Fixes #6: GitHub could not act, it was closed before the merge',
            "PR #12 120aaaaa line 1: Fixes #7: the PR's own link closed it "
            '(closingIssuesReferences), not shown to be the commit\'s keyword',
            'PR #14 140aaaaa line 1: Fixes jeffpiazza/derbynet#8: in another repo, not read',
            '5 PRs, 4 refusals, 0 of them on an issue GitHub left open'])

    def test_an_issue_left_open_by_the_merge_is_a_false_refusal(self):
        prs = [pr(10, '2026-11-10T00:00:00Z', 'Fixes #5')]
        merge = f'{10:040x}'
        events = {'5': [{'event': 'referenced', 'commit_id': merge, 'created_at': '2026-11-10T00:00:05Z'}]}
        done = self.run_sweep(1, prs, events)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [
            'PR #10 100aaaaa line 1: Fixes #5: GitHub left it open (a false refusal)',
            '1 PRs, 1 refusals, 1 of them on an issue GitHub left open'])

    def test_a_close_after_the_merge_is_no_close_by_it(self):
        prs = [pr(10, '2026-11-10T00:00:00Z', 'Fixes #5')]
        events = {'5': [{'event': 'closed', 'commit_id': None, 'created_at': '2026-11-12T00:00:00Z'}]}
        done = self.run_sweep(1, prs, events)
        self.assertEqual(done.returncode, 1, done.stderr)

    def test_the_last_n_by_merge_date_are_read_not_the_last_n_listed(self):
        prs = [pr(30, '2026-11-01T00:00:00Z', 'Fixes #5'), pr(20, '2026-11-09T00:00:00Z', 'Clean')]
        done = self.run_sweep(1, prs, {})
        self.assertEqual((done.returncode, done.stdout), (0, '1 PRs, 0 refusals, 0 of them on an issue GitHub left open\n'),
                         done.stderr)

    def test_a_gh_failure_exits_non_zero(self):
        done = subprocess.run([sys.executable, '-I', str(COMMIT_CLOSES), '--sweep', '1'],
                              capture_output=True, text=True,
                              env={**os.environ, 'GH': '/bin/false'})
        self.assertEqual(done.returncode, 2, done.stderr)

    def test_sweep_takes_a_positive_whole_number(self):
        """`--sweep 0` would read every PR, as `[-0:]` slices all of them."""
        for bad in ('0', '-1', '1.5', 'x'):
            with self.subTest(bad=bad):
                done = subprocess.run([sys.executable, '-I', str(COMMIT_CLOSES),
                                       f'--sweep={bad}'], capture_output=True, text=True)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn('takes a whole number of PRs', done.stderr)

    def test_sweep_takes_no_other_option(self):
        for other in (('--closes', '5'), ('--base', 'master'), ('--head', 'HEAD')):
            with self.subTest(other=other):
                done = subprocess.run([sys.executable, '-I', str(COMMIT_CLOSES), '--sweep', '1',
                                       *other], capture_output=True, text=True)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn('--sweep takes no other option', done.stderr)


if __name__ == '__main__':
    unittest.main()
