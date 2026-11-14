"""plan/bin/pr-evidence against a fake gh that prints an issue body.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PR_EVIDENCE = Path(__file__).resolve().parent.parent / 'bin' / 'pr-evidence'

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
    ''')

KEYS = [
    'Embedded images make up most of the `.fodt` size.',
    '`install-docker.md` was followed end to end on three machines.',
    '`dev-timer-protocol.md` lists every message.',
    '`/docs/quick-reference` loads offline.',
    'The workflow is green on this pull request.',
    'The fixture list is in the PR description.',
]


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

    def test_it_lists_the_first_sentence_of_each_open_line_that_asks(self):
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         [*KEYS, '6 open lines of #61 ask for evidence in the PR'])

    def test_a_body_without_the_checklist_is_refused(self):
        """#61's draft quoted every box but the timer guide's."""
        body = '\n\n'.join(k for k in KEYS if 'dev-timer' not in k)
        done = self.check(body)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout.splitlines(), [
            'not in the body: `dev-timer-protocol.md` lists every message.',
            '5 of 6 open lines of #61 that ask for evidence in the PR are quoted in the body'])

    def test_a_body_quoting_each_line_passes_wrapped_or_quoted(self):
        body = ('<details><summary>Embedded images make up most of the\n'
                '`.fodt` size.</summary>\n\n12 MB\n</details>\n\n'
                '> `install-docker.md` was followed end to end on\n> three machines.\n\n'
                '## Still open\n\n' + '\n'.join(f'- {k.upper()}' for k in KEYS[2:]))
        done = self.check(body)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('6 of 6', done.stdout)

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


if __name__ == '__main__':
    unittest.main()
