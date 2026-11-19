"""Each tracked file a workflow's jobs name is in that workflow's `paths:`
filter, for every trigger that has one, so a change to a file a job runs
starts the job.

#399's first head changed testing/race-with-derby-timer.sh, which server.yml's
shell jobs run, but server.yml's filters named only testing/reset-database.sh
and testing/common.sh. pr-wait went green on that head without the jobs that
run the script; golden-fixtures and php-suite filter on testing/** and ran, so
asking whether some workflow covers each changed file would have passed it
too (#399 retro).

A file a job names is any token below `jobs:`, outside a comment, that is a
tracked path from the repository root (or from `$GITHUB_WORKSPACE/`) or from
a `working-directory:` in the same file. A file a named script reads in turn is not followed. A workflow
with no `paths:` runs on every change, and is left out.

Run: python3 -I -m unittest discover -s plan/tests
"""
import re
import subprocess
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github' / 'workflows'
# The checkout's root, which a path a step names can start from.
WORKSPACE = re.compile(r'(\$GITHUB_WORKSPACE|\$\{\{ *github\.workspace *\}\})/')
# A comment: a `#` at the start of a line or after a space.
COMMENT = re.compile(r'(?:^|\s)#.*')
TOKEN = re.compile(r'(?<![\w./-])((?:[\w.-]+/)+[\w.-]+)')


def glob_pattern(glob):
    """GitHub's filter glob as a regex: `**` crosses `/`, `*` and `?` don't."""
    out = ''
    i = 0
    while i < len(glob):
        if glob.startswith('**', i):
            out += '.*'
            i += 2
        elif glob[i] == '*':
            out += '[^/]*'
            i += 1
        elif glob[i] == '?':
            out += '[^/]'
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(out + r'\Z')


def matches(path, globs):
    """Whether `globs` take `path`; the last glob that matches decides, and a
    `!` glob drops it, as GitHub reads a filter."""
    taken = False
    for glob in globs:
        if glob_pattern(glob.removeprefix('!')).match(path):
            taken = not glob.startswith('!')
    return taken


def trigger_paths(text):
    """Each trigger's `paths:` list in a workflow's `on:` block."""
    paths = {}
    trigger = None
    in_on = False
    for line in text.splitlines():
        if line.startswith('on:'):
            in_on = True
            continue
        if not in_on:
            continue
        if re.match(r'\S', line):
            break
        if found := re.match(r'  (\w+):', line):
            trigger = found[1]
        elif re.match(r'    paths:', line):
            paths[trigger] = []
        elif (found := re.match(r'      - (\S+)', line)) and trigger in paths:
            paths[trigger].append(found[1].strip('\'"'))
    return paths


def named_files(text, tracked):
    """The tracked files the jobs of a workflow name."""
    jobs = text.split('\njobs:', 1)[1]
    dirs = set(re.findall(r'working-directory:\s*(\S+)', jobs)) - {'.'}
    names = set()
    for line in jobs.splitlines():
        code = WORKSPACE.sub('', COMMENT.sub('', line))
        for token in TOKEN.findall(code):
            token = token.removeprefix('./')
            for path in [token, *(f'{d}/{token}' for d in dirs)]:
                if path in tracked:
                    names.add(path)
    return names


def unfiltered(text, tracked):
    """Each trigger with a `paths:` filter, and the named files it misses."""
    names = named_files(text, tracked)
    return {trigger: sorted(n for n in names if not matches(n, globs))
            for trigger, globs in trigger_paths(text).items()}


def tracked_files():
    out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                         check=True, capture_output=True, text=True).stdout
    return set(out.split('\0'))


# server.yml before #422 (7a38fdf5), trimmed: the filters name
# testing/reset-database.sh and testing/common.sh, and the shell job also runs
# testing/race-with-derby-timer.sh.
SERVER_BEFORE_422 = """\
name: server

on:
  push:
    branches: [master]
    paths:
      - server/**
      - testing/reset-database.sh
      - testing/common.sh
  pull_request:
    paths:
      - server/**
      - testing/reset-database.sh
      - testing/common.sh
  workflow_dispatch:

jobs:
  timer:
    steps:
      # testing/common.sh keeps its cookies in the repository root.
      - run: |
          bash testing/reset-database.sh http://127.0.0.1:8080
          bash testing/race-with-derby-timer.sh http://127.0.0.1:8080
"""
TRACKED_399 = {'testing/reset-database.sh', 'testing/common.sh',
               'testing/race-with-derby-timer.sh', 'server/manage.py'}


class WorkflowPaths(unittest.TestCase):
    def test_each_workflow_filters_on_the_files_its_jobs_name(self):
        tracked = tracked_files()
        workflows = sorted(WORKFLOWS.glob('*.y*ml'))
        self.assertIn(WORKFLOWS / 'server.yml', workflows)
        self.assertIn('testing/race-with-derby-timer.sh', named_files(
            (WORKFLOWS / 'server.yml').read_text(), tracked))
        missing = {f'{wf.name} {trigger}': files
                   for wf in workflows
                   for trigger, files in unfiltered(wf.read_text(),
                                                    tracked).items()
                   if files}
        self.assertEqual(missing, {}, 'add each file, or a glob that takes '
                         "it, to that trigger's paths: list")

    def test_server_yml_before_422_misses_the_timer_script(self):
        self.assertEqual(unfiltered(SERVER_BEFORE_422, TRACKED_399), {
            'push': ['testing/race-with-derby-timer.sh'],
            'pull_request': ['testing/race-with-derby-timer.sh']})

    def test_a_glob_that_takes_the_script_passes(self):
        text = SERVER_BEFORE_422.replace('testing/common.sh', 'testing/**')
        self.assertEqual(unfiltered(text, TRACKED_399),
                         {'push': [], 'pull_request': []})

    def test_a_comment_names_no_file(self):
        text = SERVER_BEFORE_422.replace(
            'bash testing/race-with-derby-timer.sh',
            '# bash testing/race-with-derby-timer.sh')
        self.assertEqual(named_files(text, TRACKED_399),
                         {'testing/reset-database.sh'})
        text = SERVER_BEFORE_422.replace(
            '127.0.0.1:8080\n', '127.0.0.1:8080  # testing/common.sh\n', 1)
        self.assertEqual(named_files(text, TRACKED_399), {
            'testing/reset-database.sh', 'testing/race-with-derby-timer.sh'})
        text = SERVER_BEFORE_422.replace(
            '      - run: |', '# bash testing/common.sh\n      - run: |')
        self.assertEqual(named_files(text, TRACKED_399), {
            'testing/reset-database.sh', 'testing/race-with-derby-timer.sh'})

    def test_a_path_from_the_workspace_is_named(self):
        for root in ('$GITHUB_WORKSPACE', '${{ github.workspace }}'):
            text = SERVER_BEFORE_422.replace(
                'bash testing/race', f'bash "{root}/testing/race')
            self.assertEqual(named_files(text, TRACKED_399), {
                'testing/reset-database.sh',
                'testing/race-with-derby-timer.sh'})

    def test_a_path_from_a_working_directory_is_named(self):
        text = ('on:\n  pull_request:\n    paths:\n      - testing/**\n'
                'jobs:\n  a:\n    defaults:\n      run:\n'
                '        working-directory: server\n'
                '    steps:\n      - run: uv run python ./manage.py check\n')
        self.assertEqual(unfiltered(text, TRACKED_399),
                         {'pull_request': ['server/manage.py']})

    def test_a_trigger_without_paths_is_left_out(self):
        text = ('on:\n  schedule:\n    - cron: "1 2 * * *"\n  push:\n'
                '    branches:\n      - master\njobs:\n  a:\n'
                '    steps:\n      - run: bash testing/common.sh\n')
        self.assertEqual(named_files(text, TRACKED_399), {'testing/common.sh'})
        self.assertEqual(unfiltered(text, TRACKED_399), {})

    def test_only_the_on_block_holds_triggers(self):
        text = SERVER_BEFORE_422.replace(
            '\njobs:', '\nenv:\n  push:\n    paths:\n      - other/**\n\njobs:')
        self.assertEqual(unfiltered(text, TRACKED_399), {
            'push': ['testing/race-with-derby-timer.sh'],
            'pull_request': ['testing/race-with-derby-timer.sh']})

    def test_globs_read_as_github_reads_them(self):
        self.assertTrue(matches('testing/a/b.sh', ['testing/**']))
        self.assertFalse(matches('testing/a/b.sh', ['testing/*']))
        self.assertTrue(matches('testing/b.sh', ['testing/*.sh']))
        self.assertTrue(matches('testing/b.sh', ['testing/?.sh']))
        self.assertFalse(matches('testing/bc.sh', ['testing/?.sh']))
        self.assertFalse(matches('testing/b.sh', ['testing/**', '!testing/b.sh']))
        self.assertTrue(matches('testing/b.sh',
                                ['!testing/b.sh', 'testing/**']))
        self.assertFalse(matches('testingXb.sh', ['testing.b.sh']))


if __name__ == '__main__':
    unittest.main()
