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
ISSUES = [{'number': 2, 'state': 'OPEN', 'body': 'Roadmap.', 'labels': []},
          {'number': 7, 'state': 'OPEN', 'body': TICKET, 'labels': [{'name': 'kind:ticket'}]}]

FAKE_GH = '''import json, sys
issues = {issues!r}
if sys.argv[1:3] == ['issue', 'list'] and '--label' in sys.argv:
    print(json.dumps(issues))
elif sys.argv[2] == 'list' and 'closingIssuesReferences' in ' '.join(sys.argv):
    print('[]')
else:
    print(json.dumps([{{'number': i['number']}} for i in issues]))
'''


class LintIssues(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
