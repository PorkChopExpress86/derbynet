"""heat-intermission's check.sh fails a SKILL.md state count TLC did not find.

#515's tlc-check.log and PR body gave 328,096 distinct states, while
SKILL.md's baseline table still said 190,080 in six places: step 4 of its
"Updating the model" asks for the update, and it was missed (#515 retro).
These runs give check.sh a stub `java` that prints TLC's count line, so they
need no Java or tla2tools.jar.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / '.agents' / 'skills' / 'heat-intermission'
TABLE = ('| `RacingHeatAllowed` | ... | **PASS** ({n} distinct states checked) |\n'
         '| `LockedFeederProtected` | ... | **PASS** ({m} distinct states checked) |\n'
         '\nTLC also checks `NoDeadlock` across all {k} states (**PASS**).\n')


class CheckCountsStates(unittest.TestCase):
    def run_check(self, skill_md: str, found: int = 328096, passes: bool = True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copy(SKILL / 'check.sh', tmp / 'check.sh')
            (tmp / 'HeatIntermission.tla').write_text('---- MODULE HeatIntermission ----\n')
            (tmp / 'SKILL.md').write_text(skill_md)
            (tmp / 'tla2tools.jar').write_text('')
            java = tmp / 'java'
            java.write_text('#!/bin/sh\n'
                            f'echo "9 states generated, {found} distinct states found, '
                            '0 states left on queue."\n'
                            f'exit {0 if passes else 12}\n')
            java.chmod(0o755)
            env = {**os.environ, 'JAVA': str(java), 'TLA2TOOLS': str(tmp / 'tla2tools.jar')}
            return subprocess.run([str(tmp / 'check.sh'), 'RacingHeatAllowed', 'LockedFeederProtected'],
                                  capture_output=True, text=True, env=env)

    def test_figures_that_match_the_run_pass(self):
        run = self.run_check(TABLE.format(n='328,096', m='328,096', k='328,096'))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn('STALE', run.stdout)

    def test_each_stale_figure_is_named_with_its_line(self):
        run = self.run_check(TABLE.format(n='328,096', m='190,080', k='190,080'))
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        stale = [line for line in run.stdout.splitlines() if line.startswith('STALE')]
        self.assertEqual(len(stale), 2, run.stdout)
        self.assertIn('SKILL.md:2 says 190,080 distinct states', stale[0])
        self.assertIn('SKILL.md:4 says 190,080 states', stale[1])

    def test_a_failing_invariant_reads_no_figure(self):
        # TLC stops at a violation, so the count it prints is no baseline.
        run = self.run_check(TABLE.format(n='328,096', m='328,096', k='328,096'),
                             found=12, passes=False)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn('FAIL  RacingHeatAllowed', run.stdout)
        self.assertNotIn('STALE', run.stdout)
