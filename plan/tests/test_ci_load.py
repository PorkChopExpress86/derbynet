"""plan/bin/ci-load, in process, on affected-tests' real output.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import json
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

CI_LOAD = Path(__file__).resolve().parent.parent / 'bin' / 'ci-load'
WORKFLOW = Path(__file__).resolve().parents[2] / '.github' / 'workflows' / 'ci-load.yml'


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
    """A junit file with the testsuites header pytest writes."""
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

    def test_a_trial_count_replaces_each_commands_count(self):
        plan = TOOL.plan(MASTER_KIOSKS + '  tools/pytest-under-load.sh -m browser x.py\n',
                         per_shard=1, trial_count=2)
        self.assertEqual([c['count'] for c in plan['commands']], [2, 2, 2])
        self.assertEqual([s['count'] for s in plan['shards']], [1] * 6)
        self.assertEqual(plan['shards'][0]['args'],
                         ['-m', 'browser', '-n', '4', '--count', '1',
                          'ui/tests/browser/test_kiosks.py'])
        self.assertEqual(plan['shards'][4]['args'], ['-m', 'browser', 'x.py', '--count', '1'])

    def test_a_command_with_no_count_runs_once_in_one_shard(self):
        plan = TOOL.plan('  tools/pytest-under-load.sh -m browser x.py\n')
        self.assertEqual([c['count'] for c in plan['commands']], [1])
        self.assertEqual([(s['count'], s['args']) for s in plan['shards']],
                         [(1, ['-m', 'browser', 'x.py'])])

    def test_a_prose_mention_or_a_quoted_command_is_no_load_command(self):
        text = ('Run tools/pytest-under-load.sh in the background\n'
                '  echo tools/pytest-under-load.sh --count 25 x.py\n'
                '  uv run pytest -n auto -m browser ui\n')
        plan = TOOL.plan(text)
        self.assertEqual(plan['shards'], [])
        self.assertEqual(len(plan['skipped']), 2)

    def test_no_load_command_plans_no_shard(self):
        plan = TOOL.plan('Repeats under load: no browser test file changed.\n')
        self.assertEqual(plan, {'commands': [], 'shards': [], 'skipped': [], 'notes': []})

    def test_workers_replace_the_n_of_each_command(self):
        plan = TOOL.plan(MASTER_KIOSKS + NODE_IDS, workers=8)
        self.assertEqual({tuple(s['args'][:5]) for s in plan['shards']},
                         {('-m', 'browser', '-n', '8', '--count'),
                          ('--postgres', '-m', 'browser', '-n', '8')})

    def test_a_script_from_before_d122_drops_the_flake_flag_and_says_so(self):
        old = 'case "$1" in\n    --loops) LOOPS=$2; shift 2 ;;\n'
        new = old + '    --flake-ticket) FLAKE_TICKET=--flake-ticket; shift ;;\n'
        for script, kept in ((old, False), (new, True), (None, True)):
            with self.subTest(kept=kept):
                plan = TOOL.plan(NODE_IDS, flake_ticket=True, script_text=script)
                self.assertEqual('--flake-ticket' in plan['shards'][0]['args'], kept)
                self.assertEqual(plan['notes'], [] if kept else [
                    "This head's pytest-under-load.sh has no --flake-ticket (D122): "
                    'left out.'])
        plan = TOOL.plan(NODE_IDS, script_text=old)
        self.assertEqual(plan['notes'], [])

    def test_more_than_256_shards_are_refused(self):
        line = '  tools/pytest-under-load.sh -m browser --count {} x.py\n'
        self.assertEqual(len(TOOL.plan(line.format(256), per_shard=1)['shards']), 256)
        with self.assertRaisesRegex(ValueError, '257 shards, more than the 256 jobs'):
            TOOL.plan(line.format(257), per_shard=1)


class Inputs(unittest.TestCase):
    SHA = '0123456789abcdef0123456789abcdef01234567'

    def test_good_inputs_print_github_output_lines_with_the_default_loops(self):
        self.assertEqual(TOOL.inputs(self.SHA, '', ''),
                         [f'sha={self.SHA}', 'loops_per_cpu=2', 'workers='])
        self.assertEqual(TOOL.inputs(self.SHA, '1', '99'),
                         [f'sha={self.SHA}', 'loops_per_cpu=1', 'workers=99'])

    def test_each_wrong_input_is_refused(self):
        for sha, loops, workers, why in (
                (self.SHA[:39], '', '', 'sha must be a full 40-character SHA'),
                (self.SHA + '0', '', '', 'sha must be'),
                (self.SHA.upper(), '', '', 'sha must be'),
                ('origin/master', '', '', 'sha must be'),
                (self.SHA, '0', '', 'loops_per_cpu must be a whole number from 1 to 99'),
                (self.SHA, '100', '', 'loops_per_cpu must be'),
                (self.SHA, '1.5', '', 'loops_per_cpu must be'),
                (self.SHA, '', '0', 'workers must be a whole number from 1 to 99'),
                (self.SHA, '', '4 -x', 'workers must be')):
            with self.subTest(sha=sha, loops=loops, workers=workers):
                with self.assertRaisesRegex(ValueError, why):
                    TOOL.inputs(sha, loops, workers)


class Font(unittest.TestCase):
    def test_only_a_default_font_shard_without_noto_sans_is_refused(self):
        self.assertIsNone(TOOL.font_error('', 'Noto Sans'))
        self.assertIsNone(TOOL.font_error('DejaVu Sans', 'DejaVu Sans'))
        self.assertEqual(TOOL.font_error('', 'DejaVu Sans'),
                         "the default font is 'DejaVu Sans', not Noto Sans as on the laptop")


class Sample(unittest.TestCase):
    def test_it_samples_until_a_sigterm_ends_it_with_0_and_starts_no_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'loadavg.log'
            proc = subprocess.Popen([sys.executable, '-I', str(CI_LOAD), 'sample', str(log),
                                     '--every', '0.05'])
            self.addCleanup(proc.kill)
            deadline = time.monotonic() + 10
            while (not log.exists() or len(log.read_text().splitlines()) < 2) \
                    and time.monotonic() < deadline:
                time.sleep(0.05)
            children = Path(f'/proc/{proc.pid}/task/{proc.pid}/children').read_text()
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 0)
            lines = log.read_text().splitlines()
        self.assertEqual(children, '')
        self.assertGreaterEqual(len(lines), 2)
        for line in lines:
            self.assertRegex(line, r'^\d\d:\d\d:\d\d \d+\.\d\d \d+\.\d\d \d+\.\d\d \d+/\d+ \d+$')


class Workflow(unittest.TestCase):
    def test_every_input_but_sha_and_per_shard_makes_the_run_a_trial(self):
        """pr-evidence accepts only a run named `CI load at <SHA>`: an input
        that changes what runs must change the name."""
        text = WORKFLOW.read_text()
        dispatch = text[text.index('  workflow_dispatch:'):text.index('\n  push:')]
        names = re.findall(r'^      (\w+):$', dispatch, re.M)
        self.assertEqual(names, ['sha', 'base', 'files', 'flake_ticket', 'per_shard', 'count',
                                 'loops_per_cpu', 'workers'])
        run_name = text[text.index('run-name:'):text.index('\non:')]
        condition = run_name[run_name.index("|| ("):run_name.index("&& format('CI load trial at")]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(f'inputs.{name}' in condition,
                                 name not in ('sha', 'per_shard'))


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
        self.assertNotIn('No load command', text)
        self.assertIn('Count 25, font default (Noto Sans, as the laptop).', text)
        self.assertIn('Count 25, font DejaVu Sans.', text)

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

    def test_a_note_of_the_plan_is_in_the_summary(self):
        plan = TOOL.plan(NODE_IDS, flake_ticket=True, script_text='')
        for s in plan['shards']:
            self.shard(s['name'], 0, junit(10))
        text, _failed = TOOL.summary(plan, self.dir, 'f' * 40)
        self.assertIn("This head's pytest-under-load.sh has no --flake-ticket (D122): left out.",
                      text)


def run_tool(*argv, stdin=''):
    return subprocess.run([sys.executable, '-I', str(CI_LOAD), *argv],
                          input=stdin, capture_output=True, text=True)


class CommandLine(unittest.TestCase):
    def test_plan_takes_each_option(self):
        done = run_tool('plan', '--per-shard', '2', '--count', '3', '--flake-ticket',
                        stdin=MASTER_KIOSKS)
        self.assertEqual(done.returncode, 0, done.stderr)
        shards = json.loads(done.stdout)['shards']
        self.assertEqual([s['count'] for s in shards], [2, 1, 2, 1])
        self.assertEqual(shards[0]['args'][:1], ['--flake-ticket'])
        self.assertEqual(run_tool('plan', stdin=MASTER_KIOSKS).returncode, 0)

    def test_a_count_or_shard_size_below_1_and_a_wrong_action_are_refused(self):
        for argv in (('plan', '--per-shard', '0'), ('plan', '--count', '0'),
                     ('plan', '--per-shard'), ('summary', 'plan.json', 'dir'), ('sum',),
                     ('plan', '--flake')):
            with self.subTest(argv=argv):
                done = run_tool(*argv, stdin=MASTER_KIOSKS)
                self.assertEqual(done.returncode, 2, done.stdout)
                self.assertIn('usage:', done.stderr)

    def test_inputs_font_and_plan_print_an_error_line_and_exit_1(self):
        sha = '0' * 40
        done = run_tool('inputs', sha, '', '8')
        self.assertEqual((done.returncode, done.stdout),
                         (0, f'sha={sha}\nloops_per_cpu=2\nworkers=8\n'))
        for argv, stdin, why in (
                (('inputs', 'HEAD', '', ''), '', "::error::sha must be a full 40-character SHA, not 'HEAD'"),
                (('font', '', 'DejaVu Sans'), '', '::error::the default font is'),
                (('plan', '--per-shard', '1'),
                 '  tools/pytest-under-load.sh -m browser --count 257 x.py\n',
                 '::error::257 shards')):
            with self.subTest(argv=argv):
                done = run_tool(*argv, stdin=stdin)
                self.assertEqual(done.returncode, 1, done.stderr)
                self.assertIn(why, done.stdout)
        done = run_tool('font', '', 'Noto Sans')
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("fc-match sans-serif: Noto Sans; the command's font: default", done.stdout)

    def test_plan_takes_workers_and_the_heads_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'pytest-under-load.sh'
            script.write_text('case "$1" in\n')
            done = run_tool('plan', '--flake-ticket', '--workers', '2', '--script', str(script),
                            stdin=NODE_IDS)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['shards'][0]['args'][:5],
                         ['--postgres', '-m', 'browser', '-n', '2'])
        self.assertIn('has no --flake-ticket (D122): left out.', done.stderr)

    def test_summary_exits_0_when_every_shard_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = TOOL.plan(MASTER_KIOSKS)
            (Path(tmp) / 'plan.json').write_text(json.dumps(plan))
            for s in plan['shards']:
                (Path(tmp) / s['name']).mkdir()
                (Path(tmp) / s['name'] / 'status').write_text('0\n')
            done = run_tool('summary', str(Path(tmp) / 'plan.json'), tmp, '0' * 40)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('Result: passed', done.stdout)

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
