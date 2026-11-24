"""plan/bin/ticket-overlap on the four tickets of the session retro of
2026-11-24, whose Scope and References are copied below, and against a
fake gh for the command line.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

TICKET_OVERLAP = Path(__file__).resolve().parent.parent / 'bin' / 'ticket-overlap'


def load_tool():
    loader = importlib.machinery.SourceFileLoader('ticket_overlap_tool', str(TICKET_OVERLAP))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL = load_tool()

# The cites of each ticket's Scope and References, as GitHub held them.
BODIES = {
    494: '''## Scope

- [ ] Fix the port's resume. `server/ui/static/ui/kiosk.js:175` calls `play()` ... (`server/ui/static/ui/kiosk.css:224-226`).
  - (4) PHP re-runs the place flyers (`website/js/now-racing.js:225`) ... (`kiosk-now-racing.js:195`).

## References

- `website/js/now-racing.js`, `website/js/now-racing-columnar.js`, `website/js/now-racing-poller.js`
- `server/ui/static/ui/kiosk.js`, `server/ui/static/ui/kiosk.css`, `server/ui/static/ui/kiosk-now-racing.js`
- #52, PR #493
''',
    499: '''## Scope

- [ ] Discard the current heat from the dashboard (`server/ui/dashboard/views.py:256`) ... (`~/.cache/derbynet-drafts/heat-verify/test_heatverify_scratch.py`).

## References

- `server/racing/services/results.py`, `server/racing/services/heats.py`, `server/ui/dashboard/views.py`
- `website/ajax/action.result.delete.inc`, `website/ajax/action.heat.reinstate.inc`
- `.agents/skills/heat-intermission/SKILL.md`, #41
''',
    500: '''## Scope

- [x] ... so `rebuild_waiting_final` returns early (`server/racing/services/heats.py:356`).
- [ ] Per D135, `round_ended` (`server/racing/services/playlist.py:170`) ...

## References

- `server/racing/services/playlist.py`, `server/racing/services/heats.py`, `website/inc/playlist.inc`, `website/inc/autoadvance.inc`
- `plan/owner-decisions.md` D27, `.agents/skills/heat-intermission/SKILL.md`
''',
    501: '''## Scope

- [x] Decide ... (`server/racing/engine/heat_state.py:756`).

## References

- `server/racing/services/results.py`, `server/racing/services/playlist.py`, `server/racing/engine/heat_state.py`, `server/ui/dashboard/views.py`
- `plan/owner-decisions.md` D27, D28, `.agents/skills/heat-intermission/SKILL.md`
''',
}
MODEL = '.agents/skills/heat-intermission/SKILL.md'


class Overlaps(unittest.TestCase):
    def test_the_sessions_tickets(self):
        """#499 ran beside #494; #500 and #501 waited on #499 for
        racing/services and the model, and share them with each other."""
        pairs, alone = TOOL.overlaps(BODIES)
        self.assertEqual(pairs, [
            (499, 500, [MODEL, 'server/racing/services/heats.py']),
            (499, 501, [MODEL, 'server/racing/services/results.py',
                        'server/ui/dashboard/views.py']),
            (500, 501, [MODEL, 'server/racing/services/playlist.py']),
        ])
        self.assertEqual(alone, [494])

    def test_a_folder_shares_each_path_under_it(self):
        self.assertEqual(TOOL.shared({'server/racing/services'},
                                     {'server/racing/services/heats.py'}),
                         ['server/racing/services'])
        self.assertEqual(TOOL.shared({'server/racing/services/heats.py'},
                                     {'server/racing/services/'.rstrip('/')}),
                         ['server/racing/services/heats.py'])
        self.assertEqual(TOOL.shared({'server/racing/servicesx'}, {'server/racing/services'}),
                         [])

    def test_what_a_ticket_only_reads_is_no_cite(self):
        """PHP is frozen and no agent edits an owner decision; a scratch path
        and a word with a slash name nothing in the repository."""
        body = ('## References\n\n- `website/js/now-racing.js`, `plan/owner-decisions.md` D27, '
                '`~/.cache/x/y.py`, `linux/arm64`, `server/x.py:3`\n')
        self.assertEqual(TOOL.cites(body), {'server/x.py'})

    def test_only_scope_and_references_are_read(self):
        body = ('## Why\n\n`server/a.py`\n\n## Scope\n\n`server/b.py`\n\n'
                '## Risks\n\n`server/c.py`\n\n## References\n\n`server/d.py`\n')
        self.assertEqual(TOOL.cites(body), {'server/b.py', 'server/d.py'})


FAKE_GH = '''#!{python}
import json, sys
if {fail!r}:
    sys.exit('gh: no network')
args = sys.argv[1:]
bodies = {bodies!r}
if args[:2] == ['issue', 'list']:
    print(json.dumps([{{'number': int(n), 'body': b}} for n, b in bodies.items()]))
elif args[:2] == ['issue', 'view']:
    print(json.dumps({{'body': bodies[args[2]]}}))
else:
    sys.exit('ticket-overlap ran gh ' + ' '.join(args))
'''


class CommandLine(unittest.TestCase):
    def run_tool(self, *argv, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp, 'gh')
            gh.write_text(FAKE_GH.format(python=sys.executable, fail=fail,
                                         bodies={str(n): b for n, b in BODIES.items()}))
            gh.chmod(0o755)
            return subprocess.run([sys.executable, '-I', str(TICKET_OVERLAP), *argv],
                                  env={**os.environ, 'GH': str(gh)},
                                  capture_output=True, text=True)

    def test_it_prints_each_pair_and_the_tickets_that_share_none(self):
        for argv in ((), ('494', '499', '500', '501')):
            with self.subTest(argv=argv):
                result = self.run_tool(*argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, (
                    f'#499 and #500 share: {MODEL}, server/racing/services/heats.py\n'
                    f'#499 and #501 share: {MODEL}, server/racing/services/results.py, '
                    'server/ui/dashboard/views.py\n'
                    f'#500 and #501 share: {MODEL}, server/racing/services/playlist.py\n'
                    'share none: #494\n'))

    def test_it_reads_only_the_tickets_named(self):
        """With every ticket shared, no ticket is left alone."""
        result = self.run_tool('499', '500')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f'#499 and #500 share: {MODEL}, '
                         'server/racing/services/heats.py\nshare none: none\n')

    def test_a_gh_failure_exits_2(self):
        result = self.run_tool(fail=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('gh: no network', result.stderr)

    def test_an_argument_that_is_no_number_exits_2(self):
        self.assertEqual(self.run_tool('#499').returncode, 2)


if __name__ == '__main__':
    unittest.main()
