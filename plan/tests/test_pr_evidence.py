"""plan/bin/pr-evidence against a fake gh that prints an issue body.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

PR_EVIDENCE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-evidence'


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


class IssueStandIn:
    """A `gh` that prints ISSUE as #61, and the tool run against it."""

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


class PrEvidence(IssueStandIn, unittest.TestCase):
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


class Placeholders(unittest.TestCase):
    def test_399s_loop_output_is_found(self):
        self.assertEqual(TOOL.placeholders(DRAFT_399), ['line 8: LOOP-OUTPUT'])

    def test_each_placeholder_shape_from_the_drafts_is_found(self):
        # From the #399 drafts, and each word of SUFFIXES.
        tokens = ('PROBE-PENDING', 'PUSH-RESULT', 'CASES-OUTPUT', 'X-RESULTS',
                  'HANDOFF-RUNS', 'MUTANT-TABLE', 'HANDOFF-TODO', 'NOTES-TBD',
                  'LOAD-RUN-PENDING')
        self.assertEqual({t.rsplit('-', 1)[1] for t in tokens}, set(TOOL.SUFFIXES))
        for token in tokens:
            with self.subTest(token=token):
                self.assertEqual(TOOL.placeholders(f'| R13 | none | ({token}). |'),
                                 [f'line 1: {token}'])

    def test_a_placeholder_in_any_punctuation_is_found(self):
        # The #408 review's probes, outside inline code.
        for text in ('LOOP-OUTPUT.', 'LOOP-OUTPUT,', 'LOOP-OUTPUT:', '{LOOP-OUTPUT}',
                     '_LOOP-OUTPUT_', '**LOOP-OUTPUT**', '<LOOP-OUTPUT>', '[LOOP-OUTPUT]',
                     '`a` LOOP-OUTPUT `b`', '`a LOOP-OUTPUT'):
            with self.subTest(text=text):
                self.assertEqual(TOOL.placeholders(text), ['line 1: LOOP-OUTPUT'])

    def test_code_names_and_inline_code_pass(self):
        # A name joined by _ is a code name, even where a draft used the
        # shape: the #408 review found VIEW_RACE_RESULTS (a permission in
        # server/), and the #399 drafts held HANDOFF_RUNS. From merged
        # bodies: #355's OUTPUT_CURL, #185's MAX_RUNS_PER_LANE, #333's
        # HEAD_SHA.
        body = ('OUTPUT_CURL and MAX_RUNS_PER_LANE, VIEW_RACE_RESULTS_PERMISSION, HEAD_SHA,\n'
                'The VIEW_RACE_RESULTS permission, see "VIEW_RACE_RESULTS".\n'
                'HAS_RESULT, RENDER_RACER_RESULTS, PURGE_RESULTS, ROUND_TABLE, DEFAULT_OUTPUT,\n'
                'TIMER_TEST_RESULT ok, OUTPUT_PENDING, HANDOFF_RUNS, LISTED_RESULT, NOTES_TBD,\n'
                '`X-RESULT` in code, $LOOP_OUTPUT, ${HANDOFF-RUNS}, path/LOOP-OUTPUT,\n'
                'LOOP-OUTPUTS, TABLE, OUTPUT, LOOP-OUTPUT.txt, LOOP-OUTPUT/x, DRY-RUN,\n'
                '$X-OUTPUT, x.X-OUTPUT, a-X-OUTPUT, LOOP-OUTPUT-FILE\n')
        self.assertEqual(TOOL.placeholders(body), [])

    def test_the_shapes_it_leaves_out(self):
        # The #408 review's probes the rule does not take: a placeholder that
        # is no capitalised token joined by -.
        for text in ('<LOOP OUTPUT>', '<<PENDING>>', 'TBD', 'TODO: fill', 'XXX',
                     '(paste output here)', 'Loop-Output', 'loop-output'):
            with self.subTest(text=text):
                self.assertEqual(TOOL.placeholders(text), [])


class CheckRefuses(IssueStandIn, unittest.TestCase):
    def test_399s_draft_is_refused_for_its_placeholder(self):
        done = self.check(evidence(KEYS) + '\n' + DRAFT_399)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:],
                         ['unfilled placeholder, line 17: LOOP-OUTPUT'])

    def test_the_draft_with_its_output_filled_in_passes(self):
        # Its "Closes #399." above its "## Still open" passes: the lead
        # dropped that check (AGENT-BRIEF puts undecided divergences there).
        filled = DRAFT_399.replace('LOOP-OUTPUT', '25 of 25 ok')
        done = self.check(evidence(KEYS) + '\n' + filled)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_body_with_crlf_line_ends_is_refused_for_its_placeholder(self):
        # The #408 review's crlf.md, as bytes.
        (self.tmp / 'body.md').write_bytes(
            (evidence(KEYS) + 'x\n- Manual check\nLOOP-OUTPUT\n').replace('\n', '\r\n').encode())
        done = self.run_tool('--check', str(self.tmp / 'body.md'))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:],
                         ['unfilled placeholder, line 11: LOOP-OUTPUT'])


if __name__ == '__main__':
    unittest.main()
