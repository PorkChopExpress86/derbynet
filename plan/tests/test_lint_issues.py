"""plan/lint-issues.py against a fake gh, so it runs with no network.

Run: python3 -I -m unittest discover -s plan/tests
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

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
        self.assertIn('note: refs/remotes/nowhere/master is not fetched, so cited paths were '
                      'checked in this checkout', done.stdout)


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


if __name__ == '__main__':
    unittest.main()
