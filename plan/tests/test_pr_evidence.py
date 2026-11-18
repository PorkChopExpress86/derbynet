"""plan/bin/pr-evidence against a fake gh that prints an issue body.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
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

PR_EVIDENCE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-evidence'
LINT_ISSUES = Path(__file__).resolve().parent.parent / 'lint-issues.py'


def load_tool():
    """plan/bin/pr-evidence as a module, so the checks that take text run in
    process (the pre-push hook runs these tests)."""
    loader = importlib.machinery.SourceFileLoader('pr_evidence_tool', str(PR_EVIDENCE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL = load_tool()

# #399's body draft as the implementer wrote it, before the lead's edit,
# cut to the lines the checks read.
DRAFT_399 = textwrap.dedent('''\
    Closes #399.

    ## Evidence

    - The script passes 25 runs in a row after the fix: all 25 pass at 8726c4ef, the head that holds this body. The loop's output:

      ```
      LOOP-OUTPUT
      ```

    ## Still open

    - Why the jar was quiet for about 150 s at its start in run 44181424925.
    ''')

# #61's boxes as the PR found them, one asking for nothing in the PR, one
# ticked by an earlier PR, and a Scope line.
ISSUE = textwrap.dedent('''\
    ## Scope

    - Embedded images make up most of the `.fodt` size. Compress the
      extracted images. Record the size in the PR.
    - The hosted PDFs stay up for PHP users.

    ## Acceptance criteria

    - [ ] Every row in the table has its Markdown file merged. Evidence is the docs check passing in CI.
    - [ ] `install-docker.md` was followed end to end on three machines. The PR lists the OS and the result for each.
    - [ ] `dev-timer-protocol.md` lists every message. Evidence is a reviewer's checklist in the PR.
    - [ ] `/docs/quick-reference` loads offline. Attach a screenshot at 390x844.
    - [x] The help link opens the quick reference. Evidence is a link to the run in the PR.
    - [ ] The workflow is green on this pull request. Link the run.
    - [ ] The fixture list is in the PR description.
    - [ ] A measurement report, attached to this issue with its scripts.
    - [ ] Manual check on a Pi 4. The timer races three heats. Attach the jar log.
    - [ ] Manual check on a Pi 4. The photo stand takes a photo. Attach the log.
    - [ ] The workflow is green on this pull request. Link the run.
    ''')

# Each open line that asks, once: #56's "attached to this issue" is not the
# PR's, and #59's two lines that start alike take their second sentence.
KEYS = [
    'Embedded images make up most of the `.fodt` size.',
    '`install-docker.md` was followed end to end on three machines.',
    '`dev-timer-protocol.md` lists every message.',
    '`/docs/quick-reference` loads offline.',
    'The workflow is green on this pull request.',
    'The fixture list is in the PR description.',
    'Manual check on a Pi 4. The timer races three heats.',
    'Manual check on a Pi 4. The photo stand takes a photo.',
]


def evidence(keys):
    return ''.join(f'- {k.rstrip(".")}: shown below.\n' for k in keys)


class PrEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / 'issue.md').write_text(ISSUE)
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\n'
                      'import sys\n'
                      f'assert sys.argv[1:4] == ["issue", "view", "61"], sys.argv\n'
                      f'print(open({str(self.tmp / "issue.md")!r}).read(), end="")\n')
        gh.chmod(0o755)
        self.env = {**os.environ, 'GH': str(gh)}

    def run_tool(self, *argv):
        return subprocess.run([sys.executable, '-I', str(PR_EVIDENCE), '61', *argv],
                              capture_output=True, text=True, env=self.env)

    def check(self, body):
        (self.tmp / 'body.md').write_text(body)
        return self.run_tool('--check', str(self.tmp / 'body.md'))

    def test_it_lists_each_open_line_that_asks_as_a_line_to_fill_in(self):
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         [*(f'- {k.rstrip(".")}: ' for k in KEYS),
                          '8 open lines of #61 ask for evidence in the PR'])

    def test_a_body_without_the_checklist_is_refused(self):
        """#61's draft left out the timer guide's checklist (#61 retro)."""
        done = self.check(evidence(k for k in KEYS if 'dev-timer' not in k))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [
            'no evidence in the body: `dev-timer-protocol.md` lists every message.',
            '7 of 8 open lines of #61 that ask for evidence in the PR have it in the body'])

    def test_each_line_given_evidence_or_left_open_passes(self):
        body = ('## Evidence\n\n' + evidence(KEYS[:2])
                + '- `DEV-TIMER-PROTOCOL.md`   lists every message.: the checklist below\n'
                + '  <details>12 rows</details>\n\n'
                + '## Still open\n\n' + ''.join(f'- {k} Waits on #59.\n' for k in KEYS[3:]))
        done = self.check(body)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('8 of 8', done.stdout)

    def test_a_quote_with_no_evidence_is_refused(self):
        """The review's body, "Not done: manual hardware smoke test on the
        release candidate.", passed as a quote anywhere in the body."""
        for item in ('Not done: {k}', '{k}', '{k}:', '{k}: Not done.', '{k}: TODO',
                     'See {k}: below'):
            with self.subTest(item=item):
                body = evidence(KEYS[1:]) + '- ' + item.format(k=KEYS[0].rstrip('.')) + '\n'
                done = self.check(body)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn('no evidence in the body: Embedded images', done.stdout)

    def test_a_key_under_another_section_than_still_open_is_no_evidence(self):
        body = evidence(KEYS[1:]) + '\n## Left for later\n\n- ' + KEYS[0] + '\n'
        done = self.check(body)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_an_issue_gh_cannot_read_exits_with_its_message(self):
        gh = self.tmp / 'gh'
        gh.write_text('#!/bin/sh\necho "no such issue" >&2\nexit 1\n')
        done = self.run_tool()
        self.assertEqual(done.returncode, 1)
        self.assertIn('gh could not read #61:\nno such issue', done.stderr)

    def test_a_wrong_argument_prints_the_usage(self):
        done = self.run_tool('--body', 'x.md')
        self.assertIn('Usage: plan/bin/pr-evidence', done.stderr)
        self.assertNotEqual(done.returncode, 0)


class ClosesWhileOpen(unittest.TestCase):
    def test_399s_draft_closes_its_issue_above_its_still_open(self):
        self.assertEqual(TOOL.closes_while_open(DRAFT_399), ['399'])

    def test_part_of_or_an_empty_still_open_passes(self):
        part_of = DRAFT_399.replace('Closes #399.', 'Part of #399.')
        self.assertEqual(TOOL.closes_while_open(part_of), [])
        # #380's body: "None." under the heading lists no item.
        none = DRAFT_399.split('## Still open')[0] + '## Still open\n\nNone. Q36 is D127.\n'
        self.assertEqual(TOOL.closes_while_open(none), [])
        later = DRAFT_399.replace('## Still open', '## Left for later')
        self.assertEqual(TOOL.closes_while_open(later), [])

    def test_the_section_ends_at_the_next_heading(self):
        body = ('Fixes #12 and resolves: #7\n\n## Still open\n\n## Evidence\n\n'
                '- a: b\n\n## Still open\n\n- c\n')
        self.assertEqual(TOOL.closes_while_open(body), ['7', '12'])
        self.assertEqual(TOOL.closes_while_open(body.rsplit('## Still open', 1)[0]), [])

    def test_closing_reads_as_lint_issues_reads_it(self):
        text = LINT_ISSUES.read_text()
        found = re.search(r"^CLOSING = re\.compile\((r'.*'), re\.I\)$", text, re.M)
        self.assertIsNotNone(found)
        self.assertEqual(TOOL.CLOSING.pattern, eval(found[1]))
        self.assertEqual(TOOL.CLOSING.flags & re.I, re.I)


class Placeholders(unittest.TestCase):
    def test_399s_loop_output_is_found(self):
        self.assertEqual(TOOL.placeholders(DRAFT_399), ['line 8: LOOP-OUTPUT'])

    def test_each_placeholder_shape_from_the_drafts_is_found(self):
        # From the #369, #383, #391 and #399 drafts.
        for token in ('PROBE-PENDING', 'HANDOFF_RUNS', 'LOAD_RUN_PENDING', 'PUSH-RESULT',
                      'MUTANT_TABLE', 'CASES-OUTPUT', 'LISTED_RESULT', 'X-RESULTS',
                      'HANDOFF-TODO', 'NOTES_TBD'):
            with self.subTest(token=token):
                self.assertEqual(TOOL.placeholders(f'| R13 | none | ({token}). |'),
                                 [f'line 1: {token}'])

    def test_code_names_and_inline_code_pass(self):
        # From merged bodies: #355's OUTPUT_CURL, #185's MAX_RUNS_PER_LANE,
        # #377's VIEW_RACE_RESULTS_PERMISSION, #333's HEAD_SHA.
        body = ('OUTPUT_CURL and MAX_RUNS_PER_LANE, VIEW_RACE_RESULTS_PERMISSION, HEAD_SHA,\n'
                '`X_RESULT` in code, $LOOP_OUTPUT, ${HANDOFF_RUNS}, path/LOOP-OUTPUT,\n'
                'LOOP-OUTPUTS, TABLE, OUTPUT, LOOP-OUTPUT.txt, X_PENDING()\n')
        self.assertEqual(TOOL.placeholders(body), [])


class CheckRefuses(PrEvidence):
    def test_399s_draft_is_refused_for_both(self):
        done = self.check(evidence(KEYS) + '\n' + DRAFT_399)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:], [
            'the body closes #399 and its "## Still open" section lists an item: write '
            '"Part of #399", or move a limit that leaves no box open to "## Left for later"',
            'unfilled placeholder, line 17: LOOP-OUTPUT'])

    def test_each_check_alone_is_refused(self):
        closing = DRAFT_399.replace('LOOP-OUTPUT', '25 of 25 ok')
        unfilled = DRAFT_399.replace('Closes', 'Part of')
        for body, line in ((closing, 'the body closes #399'),
                           (unfilled, 'unfilled placeholder, line 17: LOOP-OUTPUT')):
            with self.subTest(line=line):
                done = self.check(evidence(KEYS) + '\n' + body)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(len(done.stdout.splitlines()), 2, done.stdout)
                self.assertTrue(done.stdout.splitlines()[1].startswith(line), done.stdout)

    def test_the_draft_as_the_lead_fixed_it_passes(self):
        fixed = DRAFT_399.replace('Closes', 'Part of').replace('LOOP-OUTPUT', '25 of 25 ok')
        done = self.check(evidence(KEYS) + '\n' + fixed)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
