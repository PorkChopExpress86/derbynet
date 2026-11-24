"""plan/lint-issues.py against a fake gh, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

PLAN = Path(__file__).resolve().parent.parent
LINT = PLAN / 'lint-issues.py'

TICKET = '\n\n'.join(f'## {name}\n\nText.' for name in (
    'Goal', 'Current PHP behavior', 'Scope', 'Out of scope', 'Acceptance criteria',
    'Risks and notes', 'Dependencies', 'References'))

# #282's first body, its Goal and Current PHP behavior as written on
# 2026-11-11 (the issue's edit history), the rest cut to one line each.
GOAL_282 = (
    "Make the browser suite pass when one pytest worker runs a test outside "
    "`server/ui/tests/browser/` between two browser tests. Today that order fails every "
    "later browser test's setup with `SynchronousOnlyOperation`, and CI's "
    "`-n auto -m browser ui` can land it on one worker and fail an unrelated PR.")
PHP_282 = (
    "- PHP has no counterpart. This is the Django port's browser test harness.\n"
    "- During #256 (master d2355a0d), one command run from `server/` gave 52 "
    "`SynchronousOnlyOperation` errors at the setup of `test_kiosk_names.py`'s tests. "
    "It was observed once and has not been reproduced since.\n"
    "- The suspect, not yet verified, is that `server/ui/tests/browser/conftest.py` sets "
    "`DJANGO_ALLOW_ASYNC_UNSAFE` in its `_async_unsafe` fixture.")


def ticket(goal='Text.', php='Text.', risks='Text.', scope='Text.'):
    texts = {'Goal': goal, 'Current PHP behavior': php, 'Risks and notes': risks,
             'Scope': scope}
    return '\n\n'.join(f'## {name}\n\n{texts.get(name, "Text.")}' for name in (
        'Goal', 'Current PHP behavior', 'Scope', 'Out of scope', 'Acceptance criteria',
        'Risks and notes', 'Dependencies', 'References'))


ISSUES = [{'number': 2, 'state': 'OPEN', 'body': 'Roadmap.', 'labels': []},
          {'number': 7, 'state': 'OPEN', 'body': TICKET, 'labels': [{'name': 'kind:ticket'}]},
          {'number': 256, 'state': 'CLOSED', 'body': TICKET,
           'labels': [{'name': 'kind:ticket'}]},
          # Closed, so its history is left alone.
          {'number': 282, 'state': 'CLOSED', 'body': ticket(GOAL_282, PHP_282),
           'labels': [{'name': 'kind:ticket'}]},
          # Closed, so its uncited PHP claim is left alone (#346).
          {'number': 325, 'state': 'CLOSED',
           'body': ticket('`fake-timer.php` links them for every role.'),
           'labels': [{'name': 'kind:ticket'}]}]

FAKE_GH = '''import json, sys
issues = {issues!r}
if sys.argv[1:3] == ['issue', 'list'] and '--label' in sys.argv:
    print(json.dumps(issues))
elif sys.argv[2] == 'list' and 'closingIssuesReferences' in ' '.join(sys.argv):
    print('[]')
else:
    print(json.dumps([{{'number': i['number']}} for i in issues]))
'''


class Lint(unittest.TestCase):
    """Runs the lint on a draft against the fake gh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + FAKE_GH.format(issues=ISSUES))
        gh.chmod(0o755)
        self.env = {**os.environ, 'GH': str(gh)}

    def lint(self, body, *argv):
        draft = self.tmp / 'draft.md'
        draft.write_text(textwrap.dedent(body))
        return subprocess.run([sys.executable, '-I', str(LINT), *argv, '--file', f'0={draft}'],
                              capture_output=True, text=True, env=self.env)


class LintIssues(Lint):
    def test_a_draft_for_an_issue_not_created_yet_is_linted(self):
        """The agent that created #204 hit a KeyError on --file 0= (#53 retro)."""
        done = self.lint(TICKET + '\n\nSee #7.')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('0 findings', done.stdout)

    def test_a_new_draft_gets_its_findings(self):
        done = self.lint('## Goal\n\nSimply #99.')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('#0: sections', done.stdout)
        self.assertIn('#0:3: word Simply', done.stdout)
        self.assertIn('#0:3: reference #99 names no issue or PR', done.stdout)

    def test_labels_name_the_new_issue_kind(self):
        """A spec draft is checked against the spec sections."""
        done = self.lint(TICKET, '--labels', 'kind:spec')
        self.assertIn("!= ['Summary',", done.stdout)


# git's empty tree: a base that holds no file.
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


class CitedPaths(Lint):
    """A cited path is looked up on origin/master, not in the checkout the
    lint runs from (#61 retro: #307's draft passed in #61's worktree on a
    guide only that branch had)."""

    def cite(self, path, base):
        self.env['LINT_BASE'] = base
        return self.lint(TICKET + f'\n\nSee `{path}`.')

    def test_a_path_only_this_checkout_has_is_a_finding(self):
        done = self.cite('plan/README.md', EMPTY_TREE)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'#0: path `plan/README.md` is in this checkout but not on {EMPTY_TREE}; '
                      'cite it once it merges, or the lint on master fails', done.stdout)

    def test_a_path_on_the_base_passes(self):
        done = self.cite('plan/README.md', 'HEAD')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('0 findings', done.stdout)

    def test_a_folder_on_the_base_passes(self):
        done = self.cite('plan/bin/', 'HEAD')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_path_with_a_command_after_it_is_looked_up_by_its_first_word(self):
        done = self.cite('plan/bin/issue 61', 'HEAD')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_path_on_neither_is_missing(self):
        done = self.cite('plan/no-such-file.md', 'HEAD')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#0: missing path `plan/no-such-file.md`', done.stdout)

    def test_an_allowed_missing_path_is_a_note_on_any_base(self):
        done = self.cite('docs/dist/', EMPTY_TREE)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('0 findings, 1 allowed missing paths', done.stdout)

    def test_a_base_not_fetched_checks_this_checkout_and_says_so(self):
        done = self.cite('plan/README.md', 'refs/remotes/nowhere/master')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("note: refs/remotes/nowhere/master is not fetched, so the drafts' cited "
                      'paths were checked in this checkout only', done.stdout)


class CitedPathsInABranch(unittest.TestCase):
    """The lint copied into a scratch repository whose master cites
    `.github/workflows/electron.yml`, as #37 does, run from a checkout that
    deletes it or adds another path (#61 retro review)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / 'repo'
        (self.repo / 'plan').mkdir(parents=True)
        shutil.copy(LINT, self.repo / 'plan' / 'lint-issues.py')
        # The lint imports plan/php_cites.py, which reads the cite pattern
        # from server/tests/test_php_cite_lines.py (#346).
        shutil.copy(PLAN / 'php_cites.py', self.repo / 'plan' / 'php_cites.py')
        (self.repo / 'server' / 'tests').mkdir(parents=True)
        shutil.copy(PLAN.parent / 'server' / 'tests' / 'test_php_cite_lines.py',
                    self.repo / 'server' / 'tests' / 'test_php_cite_lines.py')
        (self.repo / '.github' / 'workflows').mkdir(parents=True)
        (self.repo / '.github' / 'workflows' / 'electron.yml').write_text('on: push\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.git_env = {**env, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                        'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
                        'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git('init', '-q', '-b', 'master')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *argv):
        return subprocess.run(['git', *argv], cwd=self.repo, env=self.git_env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def lint(self, live, draft=None):
        """The lint with issue 37's live body `live`, and `draft` as a new
        issue's, against the base commit."""
        issues = [*ISSUES, {'number': 37, 'state': 'OPEN', 'body': live,
                            'labels': [{'name': 'kind:ticket'}]}]
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + FAKE_GH.format(issues=issues))
        gh.chmod(0o755)
        argv = []
        if draft is not None:
            (self.tmp / 'draft.md').write_text(draft)
            argv = ['--file', f'0={self.tmp / "draft.md"}']
        return subprocess.run(
            [sys.executable, '-I', str(self.repo / 'plan' / 'lint-issues.py'), *argv],
            capture_output=True, text=True,
            # Without the GIT_DIR a pre-push hook sets, git reads the scratch
            # repository the lint sits in.
            env={**self.git_env, 'GH': str(gh), 'LINT_BASE': self.base})

    def test_a_live_issue_citing_a_path_the_branch_deletes_is_a_finding(self):
        """The review's `git rm .github/workflows/electron.yml` gave 0
        findings where master's lint gave one for each issue citing it."""
        self.git('rm', '-q', '.github/workflows/electron.yml')
        done = self.lint(TICKET + '\n\nSee `.github/workflows/electron.yml`.')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#37: missing path `.github/workflows/electron.yml`', done.stdout)

    def test_a_draft_citing_a_path_the_branch_deletes_is_a_finding(self):
        self.git('rm', '-q', '.github/workflows/electron.yml')
        done = self.lint(TICKET, TICKET + '\n\nSee `.github/workflows/electron.yml`.')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#0: missing path `.github/workflows/electron.yml`', done.stdout)

    def test_a_draft_citing_a_path_only_the_branch_adds_is_a_finding(self):
        (self.repo / 'plan' / 'new.md').write_text('')
        done = self.lint(TICKET, TICKET + '\n\nSee `plan/new.md`.')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'#0: path `plan/new.md` is in this checkout but not on {self.base}',
                      done.stdout)

    def test_a_live_issue_citing_a_path_the_branch_adds_passes(self):
        """The PR that adds a path a live issue already cites passes its own
        plan-checks."""
        (self.repo / 'plan' / 'new.md').write_text('')
        done = self.lint(TICKET + '\n\nSee `plan/new.md`.')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('0 findings', done.stdout)

    def test_paths_in_the_checkout_and_on_the_base_pass(self):
        done = self.lint(TICKET + '\n\nSee `.github/workflows/electron.yml`.',
                         TICKET + '\n\nSee `.github/workflows/electron.yml`.')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class UnverifiedCause(Lint):
    """A Goal that asserts a cause while Current PHP behavior or Scope marks
    an observation or a cause unverified (#282 retro)."""

    def test_282s_first_body_is_flagged_on_its_goal_line(self):
        done = self.lint(ticket(GOAL_282, PHP_282))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('#0:3: the Goal asserts a cause ("can land") while line 8 of '
                      '"Current PHP behavior" says "observed once"; a Goal states the symptom '
                      'to make pass and leaves the mechanism to the first box', done.stdout)
        self.assertIn('1 findings', done.stdout)

    def test_a_goal_that_states_only_the_symptom_passes(self):
        goal = GOAL_282.split(', and CI')[0] + '.'
        done = self.lint(ticket(goal, PHP_282))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_an_asserted_cause_with_nothing_marked_unverified_passes(self):
        done = self.lint(ticket(GOAL_282))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_a_mark_in_the_goal_itself_is_not_counted(self):
        done = self.lint(ticket(GOAL_282 + ' It was observed once.'))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_a_mark_in_scope_counts(self):
        done = self.lint(ticket(GOAL_282, scope='- [ ] Check the suspect first.'))
        self.assertIn('of "Scope" says "suspect"', done.stdout)

    def test_a_mark_in_risks_is_not_counted(self):
        """The Standards review of 4d459525: a Risks line is no fact the
        Goal rests on."""
        done = self.lint(ticket(GOAL_282, risks='- The fault was not yet reproduced.'))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_always_is_no_asserted_cause(self):
        """"Always" states the behaviour a Goal asks for, not a mechanism
        (the Standards review of 4d459525)."""
        done = self.lint(ticket('The kiosk always shows the heat name, as PHP does.', PHP_282))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_the_reviews_false_positives_pass(self):
        """Three bodies the Standards review of 4d459525 linted with
        `--file 0=`, each giving a finding before."""
        for body in (
                ticket('The kiosk always shows the heat name, as PHP does.',
                       risks='- The Chrome 90 behaviour is unverified.'),
                ticket('Reject bad input because the PHP does.',
                       php='- PHP rejects unverified input.'),
                ticket('A heat can fail to load due to a missing lane.',
                       risks='- Timing is only estimated at 2 s.')):
            with self.subTest(body=body.split('\n')[2]):
                done = self.lint(body)
                self.assertIn('0 findings', done.stdout, done.stdout)

    def test_quoted_and_code_words_are_left_out(self):
        goal = ('Make the `because` field and the "always on" switch save.\n\n'
                '```\nfails because it can land\n```')
        done = self.lint(ticket(goal, PHP_282))
        self.assertIn('0 findings', done.stdout, done.stdout)

    def test_a_spec_is_not_checked(self):
        done = self.lint(ticket(GOAL_282, PHP_282), '--labels', 'kind:spec')
        self.assertNotIn('the Goal asserts', done.stdout)

    def test_each_assertion_and_each_mark(self):
        for words in ('can land', 'can hit', 'cannot hit', 'can fail', 'can break',
                      'can cause', 'can trigger', 'can happen', 'because', 'is caused by',
                      'due to', 'the cause is'):
            with self.subTest(words=words):
                done = self.lint(ticket(f'Fix the run, which {words} it.', PHP_282))
                self.assertIn(f'("{words.removeprefix("is ")}")', done.stdout)
        for words in ('not yet verified', 'not yet been verified', 'not yet confirmed',
                      'not yet measured', 'not yet reproduced', 'observed once',
                      'not reproduced', 'not been reproduced', 'suspect', 'suspected'):
            with self.subTest(words=words):
                done = self.lint(ticket(GOAL_282, f'- The fault is {words}.'))
                self.assertIn(f'says "{words}"', done.stdout)


# D98's first wording (d9438939, #325) and its current one (64cdde2e).
D98_FIRST = ("The fake timer page shows its intro links to the race dashboard and the timer "
             "test only to a role that can open them, and the words as plain text otherwise, "
             "where PHP's `fake-timer.php` links them for every role.")
D98_NOW = ("PHP's `fake-timer.php:99-100` links its own targets, `coordinator.php` "
           '("Racing") and `timer.php` ("Simulated Racing"), for every role.')


class PhpCites(Lint):
    """A sentence in an open ticket or spec that says what a PHP file does
    cites the line (#346, plan/php_cites.py)."""

    def test_a_claim_without_a_cite_is_a_finding(self):
        done = self.lint(ticket(php=D98_FIRST))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#0:7: "`fake-timer.php` links" says what a PHP file does with no '
                      'file:line cite in its sentence; cite the line (`fake-timer.php:99-100`)',
                      done.stdout)
        self.assertIn('1 findings', done.stdout)

    def test_a_claim_with_a_cite_passes(self):
        done = self.lint(ticket(php=D98_NOW))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_plain_mention_passes(self):
        done = self.lint(ticket(php='The port replaces `fake-timer.php` and `timer.php`.'))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_spec_is_checked(self):
        done = self.lint(ticket(php=D98_FIRST), '--labels', 'kind:spec')
        self.assertIn('"`fake-timer.php` links" says what a PHP file does', done.stdout)

    def test_an_epic_and_a_closed_ticket_are_not_checked(self):
        """The fake gh's closed #325 makes the claim in its Goal."""
        done = self.lint(ticket(php=D98_FIRST), '--labels', 'kind:epic')
        self.assertNotIn('says what a PHP file does', done.stdout)
        self.assertIn('0 findings', done.stdout, done.stdout)


# #412's body as filed (2026-11-18), cut to the lines the check reads.
PHP_412 = ("- In CI's `pytest (postgres)` job on PR #7's head it raised `StopIteration` "
           "in `logged()`.")
BOX_412 = ("- [ ] `test_detach.py` passes 25 counts under load. Evidence is the "
           "`pytest-under-load.sh` output in the PR.")


WORKFLOWS = PLAN.parent / '.github' / 'workflows'


def postgres_job_names():
    """Every CI job name that runs on Postgres, read from the workflows: each
    top-level `name:` that has `matrix.database`, with the database `postgres`,
    `${{ matrix.K && 'S' || '' }}` as each of S and nothing, and any other
    `${{ matrix.K }}` as 2. A job form added to a workflow joins the list."""
    names = set()
    for path in sorted(WORKFLOWS.glob('*.yml')):
        for line in path.read_text().splitlines():
            m = re.match(r'^    name: (.*matrix\.database.*)$', line)
            if not m:
                continue
            forms = [m.group(1).replace('${{ matrix.database }}', 'postgres')]
            while any('${{' in f for f in forms):
                forms = [g for f in forms for g in expand(f)]
            names.update(forms)
    return names


def expand(form):
    m = re.search(r"\$\{\{ matrix\.\w+ && '([^']*)' \|\| '' \}\}", form)
    if m:
        return [form[:m.start()] + m.group(1) + form[m.end():],
                form[:m.start()] + form[m.end():]]
    m = re.search(r'\$\{\{ matrix\.\w+ \}\}', form)
    return [form[:m.start()] + '2' + form[m.end():]]


def acceptance(php, box):
    return ticket(php=php).replace('## Acceptance criteria\n\nText.',
                                   f'## Acceptance criteria\n\n{box}')


class LoadDatabase(Lint):
    """An open ticket that names a CI Postgres job names the database in
    each open box that asks for a run under load (#412 retro)."""

    def test_412s_box_is_a_finding(self):
        done = self.lint(acceptance(PHP_412, BOX_412))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#0:19: the body names CI\'s "pytest (postgres)" job while this box '
                      'asks for a run under load and names no database; write "on Postgres" '
                      '(or the database it should run on), since a flake repeats on the '
                      'database it failed on', done.stdout)
        self.assertIn('1 findings', done.stdout)

    def test_a_box_that_names_a_database_passes(self):
        for words in ('on Postgres', 'on SQLite', 'on PostgreSQL', 'on SQLite and Postgres'):
            with self.subTest(words=words):
                box = BOX_412.replace('under load', f'under load {words}')
                done = self.lint(acceptance(PHP_412, box))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_body_with_no_postgres_job_passes(self):
        for php in ('- In CI\'s `pytest (sqlite)` job it failed.', '- It failed on Postgres.'):
            with self.subTest(php=php):
                done = self.lint(acceptance(php, BOX_412))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_each_postgres_job_counts(self):
        names = postgres_job_names()
        for job in names:
            with self.subTest(job=job):
                done = self.lint(acceptance(PHP_412.replace('pytest (postgres)', job), BOX_412))
                self.assertIn(f'names CI\'s "{job}" job', done.stdout)

    def test_a_job_name_with_a_matrix_suffix_counts(self):
        """server.yml names `pytest (postgres, test endpoints)` and `shell
        (postgres, group 2)`; a name that does not match is a miss, not a pass."""
        names = postgres_job_names()
        for job in ('pytest (postgres, test endpoints)', 'shell (postgres, group 2)'):
            self.assertIn(job, names)

    def test_other_wordings_of_both_databases_pass(self):
        for words in ('on both databases', 'on both engines', 'on each database',
                      'on every database', 'on either database'):
            with self.subTest(words=words):
                box = BOX_412.replace('under load', f'under load {words}')
                done = self.lint(acceptance(PHP_412, box))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_each_spelling_of_a_load_run_counts(self):
        for box in ('- [ ] `test_detach.py` passes 25 counts under-load.',
                    '- [ ] `test_detach.py` passes 25 counts with `pytest-under-load.sh`.',
                    '- [ ] `test_detach.py` passes 25 counts Under Load.'):
            with self.subTest(box=box):
                done = self.lint(acceptance(PHP_412, box))
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn('names no database', done.stdout)

    def test_a_ticked_box_or_a_box_with_no_load_run_passes(self):
        for box in (BOX_412.replace('[ ]', '[x]'),
                    '- [ ] `test_detach.py` passes. Evidence is the log in the PR.'):
            with self.subTest(box=box):
                done = self.lint(acceptance(PHP_412, box))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_spec_is_not_checked(self):
        done = self.lint(acceptance(PHP_412, BOX_412), '--labels', 'kind:spec')
        self.assertNotIn('names no database', done.stdout)


# PR 438's line on #430 (merged 2026-11-21), and a fake gh that lists it as
# merged into master.
PR_438 = ("Part of #430. The ticket's third box (below) and its load box stay open.")
FAKE_GH_PRS = """import json, sys
issues = {issues!r}
prs = {prs!r}
if sys.argv[1:3] == ['issue', 'list'] and '--label' in sys.argv:
    print(json.dumps(issues))
elif sys.argv[2] == 'list' and 'closingIssuesReferences' in ' '.join(sys.argv):
    print(json.dumps(prs))
elif sys.argv[1] == 'pr':
    print(json.dumps([{{'number': p['number']}} for p in prs]))
else:
    print(json.dumps([{{'number': i['number']}} for i in issues]))
"""


class PartOfAReadyTicket(unittest.TestCase):
    """#430 was dispatched with its work done: merged PR 438 said "Part of
    #430", and #430 kept ready-for-agent with no "Still open" section
    (#470 retro)."""

    def lint(self, body, pr_body=PR_438, labels=('kind:ticket', 'ready-for-agent'),
             others=()):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        issues = [*ISSUES, {'number': 430, 'state': 'OPEN', 'body': body,
                            'labels': [{'name': l} for l in labels]}]
        issues += others
        # A merged PR with no body, as GitHub gives `null`.
        prs = [{'number': 438, 'state': 'MERGED', 'baseRefName': 'master',
                'headRefOid': '0' * 40, 'closingIssuesReferences': [], 'body': pr_body},
               {'number': 439, 'state': 'MERGED', 'baseRefName': 'master',
                'headRefOid': '0' * 40, 'closingIssuesReferences': [], 'body': None}]
        gh = tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + FAKE_GH_PRS.format(issues=issues, prs=prs))
        gh.chmod(0o755)
        return subprocess.run([sys.executable, '-I', str(LINT)], capture_output=True,
                              text=True, env={**os.environ, 'GH': str(gh)})

    def test_430_as_it_was_dispatched_is_a_finding(self):
        done = self.lint(TICKET + '\n\n- [ ] A box.')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#430: ready-for-agent, but merged PR #438 is part of it; add a '
                      '"## Still open" section that names what is left, or close it',
                      done.stdout)

    def test_a_still_open_section_passes(self):
        done = self.lint(TICKET + '\n\n## Still open\n\n- [ ] The load box.')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_ticket_not_ready_passes(self):
        done = self.lint(TICKET, labels=('kind:ticket', 'blocked'))
        self.assertNotIn('is part of it', done.stdout)

    def test_a_ready_ticket_no_pr_is_part_of_passes(self):
        other = {'number': 431, 'state': 'OPEN', 'body': TICKET,
                 'labels': [{'name': 'kind:ticket'}, {'name': 'ready-for-agent'}]}
        done = self.lint(TICKET + '\n\n## Still open\n\n- [ ] Box.', others=[other])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_part_of_in_code_names_no_issue(self):
        for body in ('Quotes `Part of #430`.', 'Quotes:\n\n```\nPart of #430\n```\n'):
            with self.subTest(body=body):
                done = self.lint(TICKET, pr_body=body)
                self.assertNotIn('is part of it', done.stdout)


# #400's body as the lead wrote it, citing #383's probe logs, which no
# folder held (#400 retro).
BODY_400 = ("#383's php-scratch probe saw one `kiosk.assign` per press "
            "(`~/.cache/derbynet-drafts/383/probe/php-enter-*.log`).")


class DraftsPaths(Lint):
    """A --file draft's drafts-folder path must exist on the lead's machine."""

    def setUp(self):
        super().setUp()
        self.env['HOME'] = str(self.tmp)

    def test_400s_missing_logs_are_a_finding(self):
        done = self.lint(TICKET + '\n\n' + BODY_400)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('#0: drafts path `~/.cache/derbynet-drafts/383/probe/php-enter-*.log` '
                      'is on no disk here', done.stdout)

    def test_a_live_body_s_missing_path_passes(self):
        """CI has no drafts folder, so only a --file draft is checked."""
        self.env['GH'] = self.live_gh(TICKET + '\n\n' + BODY_400)
        done = subprocess.run([sys.executable, '-I', str(LINT)], capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def live_gh(self, body):
        issues = [*ISSUES, {'number': 400, 'state': 'OPEN', 'body': body,
                            'labels': [{'name': 'kind:ticket'}]}]
        gh = self.tmp / 'gh-live'
        gh.write_text(f'#!{sys.executable}\n' + FAKE_GH.format(issues=issues))
        gh.chmod(0o755)
        return str(gh)

    def test_a_placeholder_path_passes(self):
        done = self.lint(TICKET + '\n\nLogs go in `~/.cache/derbynet-drafts/<N>/probe.log`.')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_path_that_exists_passes(self):
        logs = self.tmp / '.cache' / 'derbynet-drafts' / '383' / 'probe'
        logs.mkdir(parents=True)
        (logs / 'php-enter-1.log').write_text('')
        done = self.lint(TICKET + '\n\n' + BODY_400)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
