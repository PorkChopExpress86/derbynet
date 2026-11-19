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
tracked path from the repository root (or from `$GITHUB_WORKSPACE/`), or from
the folder the step runs in: the step's `working-directory:`, else the job's
`defaults:`, else the workflow's, and then each `cd DIR` before it in the
step. A file a named script reads in turn is not followed. A workflow with no
`paths:` runs on every change, and is left out.

A token in an `echo` or an artifact name counts too. That over-flags, which
is safe: it only asks for a `paths:` entry. A local action or reusable
workflow (`uses: ./...`) is not followed, none exists today, so one fails the
test. So does a workflow this reader cannot read (`paths-ignore:`, a `paths:`
that is not a block or one-line `[a, b]` list), naming the workflow.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import posixpath
import re
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github' / 'workflows'
# The checkout's root, which a path a step names can start from.
WORKSPACE = re.compile(r'(\$GITHUB_WORKSPACE|\$\{GITHUB_WORKSPACE\}'
                       r'|\$\{\{ *github\.workspace *\}\})/')
# A comment: a `#` at the start of a line or after a space.
COMMENT = re.compile(r'(?:^|\s)#.*')
TOKEN = re.compile(r'(?<![\w./-])((?:[\w.-]+/)*[\w.-]+)')
# Where one command ends and the next starts, on one line.
COMMANDS = re.compile(r'&&|\|\||;')
# The end of a command that is `cd DIR`; `sub` is set inside `( ... )`.
CD = re.compile(r'(?:^|(?P<sub>\()|[\s{])cd\s+(?P<dir>[^\s)]+)\s*\)*\s*$')
LOCAL_USES = re.compile(r'^\s*(?:-\s+)?uses:\s*[\'"]?\./')
WORKING_DIRECTORY = re.compile(r'(?:^|\s)working-directory:\s*(\S+)')


class Unreadable(Exception):
    """A workflow this reader cannot read, so it must not pass it."""


def indent(line):
    return len(line) - len(line.lstrip())


def code(line):
    """A line without its comment; empty for a blank or comment-only line."""
    return COMMENT.sub('', line).rstrip()


def fixed_dir(value):
    """The folder a `working-directory:` or `cd` names, or None when it is
    not a fixed path from here (a variable, `~`, an absolute path, `-`)."""
    value = value.strip('\'"')
    if not re.fullmatch(r'[\w.][^$~]*', value):
        return None
    return posixpath.normpath(value)


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


def on_block(text):
    """The `on:` block's lines without comments or blanks, or the one-line
    value after `on:`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if found := re.match(r'''["']?on["']?:(.*)''', code(line)):
            if 'paths' in found[1]:
                raise Unreadable('`on:` is a one-line mapping with a paths '
                                 'filter; write it as a block')
            block = []
            for later in lines[i + 1:]:
                if code(later) and indent(later) == 0:
                    break
                if code(later):
                    block.append(code(later))
            return block
    return []


def filter_items(rest, following, pad):
    """The globs of one `paths:` filter: `[a, b]` on its line, else the `- a`
    lines that follow."""
    if rest.startswith('[') and rest.endswith(']'):
        return [g.strip().strip('\'"') for g in rest[1:-1].split(',')
                if g.strip()]
    if rest:
        raise Unreadable(f'`paths: {rest}` is neither a block list nor a '
                         'one-line [a, b] list')
    items = []
    for line in following:
        if indent(line) < pad or (indent(line) == pad
                                  and not line.lstrip().startswith('-')):
            break
        found = re.match(r'\s*-\s+(\S.*)', line)
        if not found:
            raise Unreadable(f'`paths:` has a line that is not `- glob`: '
                             f'{line.strip()}')
        items.append(found[1].strip('\'"'))
    return items


def trigger_paths(text):
    """Each trigger's `paths:` list in a workflow's `on:` block."""
    paths = {}
    lines = on_block(text)
    trigger = child = None
    for i, line in enumerate(lines):
        pad = indent(line)
        body = line.strip()
        if pad == indent(lines[0]):
            found = re.match(r'([\w-]+):', body)
            trigger = found[1] if found else None
            child = None
            continue
        child = pad if child is None else child
        if pad != child:
            continue
        if body.startswith('paths-ignore'):
            raise Unreadable(f'{trigger} has paths-ignore:, which this '
                             'reader does not follow')
        if found := re.match(r'paths:\s*(.*)', body):
            paths[trigger] = filter_items(found[1], lines[i + 1:], pad)
    return paths


def first_indent(lines):
    """The indent of the first content line, or -1 when there is none."""
    return next((indent(line) for line in lines if code(line)), -1)


def blocks(lines, pad):
    """`lines` cut at each content line indented `pad`."""
    out = []
    for line in lines:
        if code(line) and indent(line) == pad:
            out.append([])
        if out:
            out[-1].append(line)
    return out


def jobs_of(jobs):
    """Each job's lines of the text after `jobs:`."""
    body = []
    for line in jobs.splitlines()[1:]:
        if code(line) and indent(line) == 0:
            break
        body.append(line)
    return blocks(body, first_indent(body))


def steps_of(job):
    """A job's lines outside its `steps:`, and each step's lines."""
    child = first_indent(job[1:])
    at = next((i for i, line in enumerate(job)
               if code(line) == ' ' * child + 'steps:'), None)
    if at is None:
        return job, []
    inside = []
    for line in job[at + 1:]:
        if code(line) and indent(line) <= child \
                and not code(line).lstrip().startswith('-'):
            break
        inside.append(line)
    steps = blocks(inside, first_indent(inside))
    return job[:at + 1] + job[at + 1 + len(inside):], steps


def first_dir(lines, default):
    for line in lines:
        if found := WORKING_DIRECTORY.search(code(line)):
            return fixed_dir(found[1]) or default
    return default


def files_in(lines, folder, tracked):
    """The tracked files `lines` name, from `folder` and each `cd` in them."""
    found = set()
    for line in lines:
        text = WORKSPACE.sub('', code(line))
        line_start = folder
        subshell = False
        for command in COMMANDS.split(text):
            for token in TOKEN.findall(command):
                for base in {folder, '.'}:
                    path = posixpath.normpath(posixpath.join(base, token))
                    if path in tracked:
                        found.add(path)
            if cd := CD.search(command):
                if target := fixed_dir(cd['dir']):
                    folder = posixpath.normpath(posixpath.join(folder, target))
                if cd['sub']:
                    subshell = True
        if subshell:
            folder = line_start
    return found


def named_files(text, tracked):
    """The tracked files the jobs of a workflow name."""
    head, found, jobs = f'\n{text}'.partition('\njobs:')
    if not found:
        raise Unreadable('has no jobs: block')
    for line in jobs.splitlines():
        if LOCAL_USES.match(line):
            raise Unreadable(f'`{line.strip()}` is a local action or '
                             'workflow, which this reader does not follow')
    workflow_dir = first_dir(head.splitlines(), '.')
    names = set()
    for job in jobs_of(jobs):
        outside, steps = steps_of(job)
        job_dir = first_dir(outside, workflow_dir)
        names |= files_in(outside, '.', tracked)
        for step in steps:
            names |= files_in(step, first_dir(step, job_dir), tracked)
    return names


def unfiltered(text, tracked, name='workflow'):
    """Each trigger with a `paths:` filter, and the named files it misses."""
    try:
        names = named_files(text, tracked)
        filters = trigger_paths(text)
    except Unreadable as error:
        raise Unreadable(f'{name}: {error}') from None
    return {trigger: sorted(n for n in names if not matches(n, globs))
            for trigger, globs in filters.items()}


def tracked_files():
    # Under a pre-push hook GIT_DIR names the main clone's git folder, and
    # git would list its index, not this tree's.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                         check=True, capture_output=True, text=True,
                         env=env).stdout
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

    def test_tracked_files_keeps_the_environment_but_git_s_own(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(kwargs['env'])
            return subprocess.CompletedProcess(argv, 0, 'a\0b', '')

        with unittest.mock.patch.dict(os.environ, {
                'GIT_DIR': '/nowhere', 'HOME': '/home/x'}), \
                unittest.mock.patch.object(subprocess, 'run', run):
            self.assertEqual(tracked_files(), {'a', 'b'})
        self.assertEqual(seen[0]['HOME'], '/home/x')
        self.assertNotIn('GIT_DIR', seen[0])

    def test_tracked_files_reads_the_checkout_without_a_hooks_git_dir(self):
        # Under a pre-push hook GIT_DIR names the main clone's git folder.
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {
                    'GIT_DIR': str(Path(tmp) / 'nowhere'),
                    'GIT_INDEX_FILE': str(Path(tmp) / 'index')}):
            self.assertIn('plan/tests/test_workflow_paths.py',
                          tracked_files())

    def test_a_cd_names_the_folder_a_script_runs_from(self):
        tracked = {'testing/common.sh', 'server/tools/a.sh', 'tools/c.sh'}
        for run, names in [
            ('cd testing && bash common.sh', {'testing/common.sh'}),
            ('cd server && bash tools/a.sh', {'server/tools/a.sh'}),
            ('cd testing; cd ../server && bash tools/a.sh',
             {'server/tools/a.sh'}),
            ('cd testing || exit 1', set()),
            ('cd testing && cd "$X" && bash common.sh', {'testing/common.sh'}),
            ('cd testing && cd /tmp && bash common.sh', {'testing/common.sh'}),
            ('cd testing && cd ~ && bash common.sh', {'testing/common.sh'}),
            ('cd "$RUNNER_TEMP" && bash tools/c.sh', {'tools/c.sh'}),
            ('x=$(cd server && bash tools/a.sh)', {'server/tools/a.sh'}),
        ]:
            text = f'jobs:\n  a:\n    steps:\n      - run: {run}\n'
            self.assertEqual(named_files(text, tracked), names, run)

    def test_a_cd_on_its_own_line_holds_for_the_step(self):
        tracked = {'testing/common.sh', 'server/tools/a.sh'}
        text = ('jobs:\n  a:\n    steps:\n      - run: |\n'
                '          cd testing\n          bash common.sh\n'
                '      - run: bash common.sh\n')
        self.assertEqual(named_files(text, tracked), {'testing/common.sh'})
        text = text.replace('bash common.sh\n      - run: bash common.sh',
                            'true\n      - run: bash common.sh')
        self.assertEqual(named_files(text, tracked), set())

    def test_a_subshell_cd_ends_with_its_line(self):
        tracked = {'testing/common.sh', 'common.sh'}
        text = ('jobs:\n  a:\n    steps:\n      - run: |\n'
                '          (cd testing && true)\n          bash common.sh\n')
        self.assertEqual(named_files(text, tracked), {'common.sh'})

    def test_a_working_directory_holds_for_its_job_or_step_only(self):
        tracked = {'server/tools/a.sh', 'testing/tools/b.sh'}
        text = ('defaults:\n  run:\n    working-directory: server\n'
                'jobs:\n'
                '  a:\n    steps:\n      - run: bash tools/a.sh\n'
                '      - run: bash tools/b.sh\n\n  # the second job\n'
                '  b:\n    defaults:\n      run:\n'
                '        working-directory: testing\n'
                '    steps:\n      - run: bash tools/b.sh\n')
        self.assertEqual(named_files(text, tracked),
                         {'server/tools/a.sh', 'testing/tools/b.sh'})
        only_a = text[:text.index('  b:\n')]
        self.assertEqual(named_files(only_a, tracked), {'server/tools/a.sh'})
        only_b = text[:text.index('  a:\n')] + text[text.index('  b:\n'):]
        self.assertEqual(named_files(only_b, tracked), {'testing/tools/b.sh'})
        step = ('jobs:\n  a:\n    defaults:\n      run:\n'
                '        working-directory: server\n    steps:\n'
                '      - run: bash tools/b.sh\n'
                '        working-directory: testing\n'
                '      - working-directory: server\n'
                '        run: bash tools/a.sh\n')
        self.assertEqual(named_files(step, tracked),
                         {'testing/tools/b.sh', 'server/tools/a.sh'})
        only_b = step.replace('      - working-directory: server\n'
                              '        run: bash tools/a.sh\n', '')
        self.assertEqual(named_files(only_b, tracked), {'testing/tools/b.sh'})

    def test_a_working_directory_that_is_not_fixed_keeps_the_default(self):
        tracked = {'server/tools/a.sh', 'testing/common.sh'}
        text = ('defaults:\n  run:\n    working-directory: server\n'
                'jobs:\n  a:\n    steps:\n'
                '      - run: bash tools/a.sh\n'
                '        working-directory: ${{ matrix.dir }}\n'
                '      - run: bash "$GITHUB_WORKSPACE/testing/common.sh"\n')
        self.assertEqual(named_files(text, tracked),
                         {'server/tools/a.sh', 'testing/common.sh'})

    def test_the_layout_around_jobs_and_steps_is_read(self):
        tracked = {'server/tools/a.sh', 'server/tools/d.sh',
                   'testing/tools/b.sh', 'testing/common.sh', 'tools/c.sh'}
        text = '''jobs:
# a comment at the margin

  # about the first job
  a:
# a comment at the margin
    defaults:
      run:
        working-directory: server
  # about the job, at the job's own indent
    steps:

      # about the step
      - run: bash tools/a.sh
    env:
      FROM_ENV: tools/d.sh

  # about the second job
  b:
    defaults:
      run:
        working-directory: testing
    steps:
    - run: bash tools/b.sh
    env:
      FROM_ENV: tools/a.sh
  c:
    env:
      FROM_ENV: testing/common.sh
env:
  AFTER_JOBS: tools/c.sh
'''
        self.assertEqual(named_files(text, tracked), {
            'server/tools/a.sh', 'testing/tools/b.sh', 'testing/common.sh'})

    def test_a_flow_list_and_other_indents_are_read(self):
        flow = SERVER_BEFORE_422.replace(
            '    paths:\n      - server/**\n      - testing/reset-database.sh\n'
            '      - testing/common.sh\n',
            "    paths: [server/**, 'testing/reset-database.sh', "
            '"testing/common.sh"]\n')
        self.assertNotEqual(flow, SERVER_BEFORE_422)
        self.assertEqual(trigger_paths(flow)['push'], [
            'server/**', 'testing/reset-database.sh', 'testing/common.sh'])
        self.assertEqual(unfiltered(flow, TRACKED_399), {
            'push': ['testing/race-with-derby-timer.sh'],
            'pull_request': ['testing/race-with-derby-timer.sh']})
        wide = re.sub(r'(?m)^( +)', lambda m: m[1] * 2, SERVER_BEFORE_422)
        self.assertEqual(unfiltered(wide, TRACKED_399),
                         unfiltered(SERVER_BEFORE_422, TRACKED_399))
        flush = SERVER_BEFORE_422.replace('    paths:\n      - ',
                                          '    paths:\n    - ')
        self.assertEqual(trigger_paths(flush)['push'][0], 'server/**')
        sibling = flush.replace('    paths:', '    branches: [master]\n'
                                '    paths:', 1).replace(
            '      - testing/common.sh\n  pull', '      - testing/common.sh\n'
            '    branches: [x]\n  pull', 1)
        self.assertEqual(trigger_paths(sibling)['push'][-1],
                         'testing/common.sh')
        trailing = 'on:\n  push:\n    paths: [server/**, ]\n'
        self.assertEqual(trigger_paths(trailing), {'push': ['server/**']})

    def test_a_comment_in_the_on_block_does_not_end_it(self):
        text = SERVER_BEFORE_422.replace(
            '  pull_request:', '# a comment at the margin\n  pull_request:')
        self.assertEqual(sorted(trigger_paths(text)),
                         ['pull_request', 'push'])

    def test_an_input_named_paths_is_not_a_filter(self):
        text = ('on:\n  workflow_dispatch:\n    inputs:\n      paths:\n'
                '        description: x\njobs:\n  a:\n    steps: []\n')
        self.assertEqual(trigger_paths(text), {})

    def test_a_workflow_it_cannot_read_fails_naming_the_workflow(self):
        for old, new, why in [
            ('    paths:\n      - server/**\n', '    paths: "server/**"\n',
             'paths: "server/\\*\\*"'),
            ('    paths:\n      - server/**\n',
             '    paths: [server/**,\n      testing/**]\n', 'paths: '),
            ('    paths:\n      - server/**\n', '    paths: server/**]\n',
             'paths: server'),
            ('    paths:\n      - server/**\n',
             '    paths-ignore:\n      - server/**\n', 'paths-ignore'),
            ('    paths:\n      - server/**\n',
             '    paths:\n      server: x\n', 'not `- glob`'),
            ('on:\n  push:', 'on: {push: {paths: [a]}}\n_:\n  push:',
             'one-line mapping'),
            ('\njobs:', '\nrun:', 'no jobs'),
            ('      - run: |', '      - uses: ./.github/actions/x\n'
             '      - run: |', 'local action'),
            ('  timer:', '  timer:\n    uses: "./.github/workflows/x.yml"',
             'local action'),
        ]:
            text = SERVER_BEFORE_422.replace(old, new, 1)
            self.assertNotEqual(text, SERVER_BEFORE_422, why)
            with self.assertRaisesRegex(Unreadable, 'wf.yml: ') as caught:
                unfiltered(text, TRACKED_399, 'wf.yml')
            self.assertRegex(str(caught.exception), why)

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
