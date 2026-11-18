"""plan/bin/ci-load, in process, on affected-tests' real output.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

CI_LOAD = Path(__file__).resolve().parent.parent / 'bin' / 'ci-load'


def load_tool():
    loader = importlib.machinery.SourceFileLoader('ci_load_tool', str(CI_LOAD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


TOOL = load_tool()

# `echo server/ui/tests/browser/test_kiosks.py | plan/bin/affected-tests
# --stdin` at 19fea1fb, its load section.
MASTER_KIOSKS = textwrap.dedent('''\
    Repeats under load (D85), on the changed test files only: the tests each adds or changes, by node id, and those that read a helper it adds or changes (D101); none for an import-only move (D97). A flake ticket keeps 25 of the whole file, on the database it failed on, whatever this prints (owner, 2026-11-11):
      (--stdin has no BASE to compare with, so no file counts as an import-only move, D97: each keeps its count, of the whole file, D101)
      ui/tests/browser/test_kiosks.py: 25 repeats, involves timing (line 129: expect_response)
          the whole file (D101): --stdin has no BASE to compare with
          tools/pytest-under-load.sh -m browser -n 4 --count 25 ui/tests/browser/test_kiosks.py
          DERBYNET_BROWSER_FONT="DejaVu Sans" tools/pytest-under-load.sh -m browser -n 4 --count 25 ui/tests/browser/test_kiosks.py

    Run from server/ (start a run longer than a minute in the background):
      uv run pytest -n auto -m browser ui/tests/browser/test_kiosks.py
    ''')

# The same at 12ae4041 (D133's branch): DejaVu Sans runs once, with no load.
D133_KIOSKS = textwrap.dedent('''\
      ui/tests/browser/test_kiosks.py: 25 repeats, involves timing (line 129: expect_response)
          the whole file (D101): --stdin has no BASE to compare with
          tools/pytest-under-load.sh -m browser -n 4 --count 25 ui/tests/browser/test_kiosks.py
          D133: a kiosk file repeats under load in the default font only; DejaVu Sans runs it once, with no load and no --count, holding the load lock:
          DERBYNET_BROWSER_FONT="DejaVu Sans" tools/hold-load-lock.sh uv run pytest -n auto -m browser ui/tests/browser/test_kiosks.py
    ''')

# A D101 run by node id on Postgres, as #390's body quotes the form.
NODE_IDS = ('      tools/pytest-under-load.sh --postgres -m browser -n 4 --count 5 '
            'ui/tests/browser/test_a.py::test_one ui/tests/browser/test_a.py::test_two\n')


def junit(tests, failures=0, errors=0, skipped=0):
    """A junit file shaped as pytest writes one (316/load/junit.xml)."""
    return ('<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
            f'<testsuite name="pytest" errors="{errors}" failures="{failures}" '
            f'skipped="{skipped}" tests="{tests}" time="1.0"></testsuite></testsuites>')


class Plan(unittest.TestCase):
    def test_a_25_count_splits_into_5_shards_of_5_in_each_font(self):
        plan = TOOL.plan(MASTER_KIOSKS)
        self.assertEqual([(c['font'], c['count']) for c in plan['commands']],
                         [(None, 25), ('DejaVu Sans', 25)])
        self.assertEqual([(s['command'], s['font'], s['count']) for s in plan['shards']],
                         [(0, '', 5)] * 5 + [(1, 'DejaVu Sans', 5)] * 5)
        self.assertEqual(plan['shards'][0]['args'],
                         ['-m', 'browser', '-n', '4', '--count', '5',
                          'ui/tests/browser/test_kiosks.py'])
        self.assertEqual(len({s['name'] for s in plan['shards']}), 10)
        self.assertEqual(plan['skipped'], [])

    def test_the_d133_once_run_is_skipped_with_its_reason(self):
        plan = TOOL.plan(D133_KIOSKS)
        self.assertEqual([c['font'] for c in plan['commands']], [None])
        self.assertEqual(len(plan['shards']), 5)
        [skip] = plan['skipped']
        self.assertIn('tools/hold-load-lock.sh uv run pytest', skip['line'])
        self.assertIn('D133', skip['why'])

    def test_shard_counts_sum_to_the_command_count(self):
        for total, per, counts in ((25, 5, [5] * 5), (5, 5, [5]), (7, 5, [4, 3]),
                                   (2, 1, [1, 1]), (1, 5, [1])):
            with self.subTest(total=total, per=per):
                self.assertEqual(TOOL.split_count(total, per), counts)
                self.assertEqual(sum(counts), total)

    def test_node_ids_postgres_and_the_flake_flag_stay_in_each_shard(self):
        plan = TOOL.plan(NODE_IDS, per_shard=2, flake_ticket=True)
        self.assertEqual([s['count'] for s in plan['shards']], [2, 2, 1])
        self.assertEqual(plan['shards'][2]['args'],
                         ['--flake-ticket', '--postgres', '-m', 'browser', '-n', '4',
                          '--count', '1', 'ui/tests/browser/test_a.py::test_one',
                          'ui/tests/browser/test_a.py::test_two'])

    def test_a_prose_mention_or_a_quoted_command_is_no_load_command(self):
        text = ('Run tools/pytest-under-load.sh in the background\n'
                '  echo tools/pytest-under-load.sh --count 25 x.py\n'
                '  uv run pytest -n auto -m browser ui\n')
        plan = TOOL.plan(text)
        self.assertEqual(plan['shards'], [])
        self.assertEqual(len(plan['skipped']), 2)

    def test_no_load_command_plans_no_shard(self):
        plan = TOOL.plan('Repeats under load: no browser test file changed.\n')
        self.assertEqual(plan, {'commands': [], 'shards': [], 'skipped': []})


class Summary(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', str(self.dir)]))
        self.plan = TOOL.plan(MASTER_KIOSKS)

    def shard(self, name, status, junit_text=None):
        folder = self.dir / name
        folder.mkdir()
        if status is not None:
            (folder / 'status').write_text(f'{status}\n')
        if junit_text:
            (folder / 'junit.xml').write_text(junit_text)

    def all_pass(self):
        for s in self.plan['shards']:
            self.shard(s['name'], 0, junit(240))

    def test_all_shards_passing_passes_and_names_head_commands_and_totals(self):
        self.all_pass()
        text, failed = TOOL.summary(self.plan, self.dir, 'a' * 40)
        self.assertFalse(failed)
        self.assertIn(f'## CI load at {"a" * 40}', text)
        for command in self.plan['commands']:
            self.assertIn(f'`{command["line"]}`', text)
        self.assertEqual(text.count('| total | 25 | | 1200 | 0 | 0 | 0 |'), 2)
        self.assertIn('Result: passed', text)
        self.assertIn('until the owner amends D85', text)

    def test_one_failing_shard_fails_the_run(self):
        self.all_pass()
        name = self.plan['shards'][3]['name']
        (self.dir / name / 'status').write_text('1\n')
        (self.dir / name / 'junit.xml').write_text(junit(240, failures=1))
        text, failed = TOOL.summary(self.plan, self.dir, 'b' * 40)
        self.assertTrue(failed)
        self.assertIn(f'| {name} | 5 | 1 | 240 | 1 | 0 | 0 |', text)
        self.assertIn('Result: failed', text)

    def test_a_shard_that_left_no_status_fails_the_run(self):
        self.all_pass()
        subprocess.run(['rm', '-rf', str(self.dir / self.plan['shards'][9]['name'])])
        text, failed = TOOL.summary(self.plan, self.dir, 'c' * 40)
        self.assertTrue(failed)
        self.assertIn('| none | no junit |', text)

    def test_the_exit_code_decides_not_junit(self):
        """D122 can exit 0 on a run whose junit holds failures."""
        self.all_pass()
        name = self.plan['shards'][0]['name']
        (self.dir / name / 'junit.xml').write_text(junit(240, failures=2))
        _text, failed = TOOL.summary(self.plan, self.dir, 'd' * 40)
        self.assertFalse(failed)

    def test_no_load_command_fails(self):
        text, failed = TOOL.summary(TOOL.plan(''), self.dir, 'e' * 40)
        self.assertTrue(failed)
        self.assertIn('No load command', text)

    def test_skipped_lines_are_named_in_the_summary(self):
        plan = TOOL.plan(D133_KIOSKS)
        for s in plan['shards']:
            self.shard(s['name'], 0, junit(240))
        text, _failed = TOOL.summary(plan, self.dir, 'f' * 40)
        self.assertIn('Skipped `DERBYNET_BROWSER_FONT="DejaVu Sans" tools/hold-load-lock.sh', text)


class CommandLine(unittest.TestCase):
    def test_plan_reads_stdin_and_summary_exits_1_on_a_failure(self):
        done = subprocess.run([sys.executable, '-I', str(CI_LOAD), 'plan'],
                              input=MASTER_KIOSKS, capture_output=True, text=True, check=True)
        plan = json.loads(done.stdout)
        self.assertEqual(len(plan['shards']), 10)
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = Path(tmp) / 'plan.json'
            plan_file.write_text(done.stdout)
            summed = subprocess.run([sys.executable, '-I', str(CI_LOAD), 'summary',
                                     str(plan_file), tmp, '0' * 40],
                                    capture_output=True, text=True)
        self.assertEqual(summed.returncode, 1, summed.stderr)
        self.assertIn('Result: failed', summed.stdout)


if __name__ == '__main__':
    unittest.main()
