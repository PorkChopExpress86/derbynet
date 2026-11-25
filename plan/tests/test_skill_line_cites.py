"""A skill under .agents/skills names a function of the port, not a line.

A skill's model describes the code at its sync commit, and moves to a new
one only after a PR merges, while each commit moves the lines under it: at
1af42150 the heat-intermission model held 32 line cites, such as
`heats.py:342-358`, already shifted against its sync commit, and review
sent them back (#501 retro). tests/test_php_cite_lines.py checks the port's own
cites, and needs a folder before a `.py` name, so it never read these.

A cite into the PHP tree (`playlist.inc:85`) may name a line, since that
tree is frozen.

Run: python3 -I -m unittest discover -s plan/tests
"""
import re
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused
from test_skill_sync_commits import git

ROOT = Path(__file__).resolve().parents[2]
# `heats.py:342`, `heats.py:342-358`, `heat_state.py line 717`, `x.tla, lines 3-4`.
CITE = re.compile(
    r'(?:(?:[\w-]+/)*[\w.-]+\.(?:py|js|mjs|css|html|kiosk|tla|cfg|sh|md))'
    r'(?:`?:|`?,? lines? )\d+(?:-\d+)?')


def line_cites(text: str) -> list[tuple[int, str]]:
    """(line number, cite) for each line cite in `text`."""
    return [(n, m[0]) for n, line in enumerate(text.splitlines(), 1)
            for m in CITE.finditer(line)]


def skill_files(root: Path) -> list[str]:
    return git(root, 'ls-files', '-z', '--', '.agents/skills').stdout.split('\0')[:-1]


class SkillLineCites(unittest.TestCase):
    def test_no_skill_cites_a_line_of_the_port(self):
        found = []
        for name in skill_files(ROOT):
            text = (ROOT / name).read_text()
            found += [f'{name}:{n}: {cite}' for n, cite in line_cites(text)]
        self.assertEqual(found, [], 'name the function, or the TABLE row, instead of its '
                         'line: lines move with every commit, the sync commit only after a '
                         'merge (#501 retro)')

    def test_the_cites_of_1af42150_are_found(self):
        text = ('(* Rebuild waiting final (D27): heats.py:342-358, playlist.py:319-360 *)\n'
                '    /\\ x  \\* locked_failure in results.py:198\n'
                '`heat_state.py` line 717, and `HeatIntermission.tla`, lines 3-4\n')
        self.assertEqual(line_cites(text), [
            (1, 'heats.py:342-358'), (1, 'playlist.py:319-360'), (2, 'results.py:198'),
            (3, 'heat_state.py` line 717'), (3, 'HeatIntermission.tla`, lines 3-4')])

    def test_each_kind_of_port_file_is_read(self):
        text = ('server/racing/services/heats.py:12 view.mjs:3 a.js:1 b.css:2 c.html:4\n'
                'd.kiosk:5 HeatIntermission.cfg:6 check.sh:7 SKILL.md:8\n')
        self.assertEqual([cite for _, cite in line_cites(text)], [
            'server/racing/services/heats.py:12', 'view.mjs:3', 'a.js:1', 'b.css:2', 'c.html:4',
            'd.kiosk:5', 'HeatIntermission.cfg:6', 'check.sh:7', 'SKILL.md:8'])

    def test_the_skills_files_are_read(self):
        self.assertIn('.agents/skills/heat-intermission/HeatIntermission.tla', skill_files(ROOT))

    def test_a_function_a_php_line_and_a_sha_are_no_cite(self):
        text = ('select_heat in heats.py, the "select heat" row of TABLE\n'
                'D135, where PHP keeps the roster (playlist.inc:85)\n'
                'website/inc/playlist.inc:85 and `kiosks.inc` line 27\n'
                'git diff --stat 603cf1fa -- server/racing/services/heats.py\n'
                'heats.py:transition, and 4461173 states\n')
        self.assertEqual(line_cites(text), [])

