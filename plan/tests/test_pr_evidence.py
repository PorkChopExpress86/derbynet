"""plan/bin/pr-evidence against a fake gh that prints an issue body.

Run: python3 -I -m unittest discover -s plan/tests
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
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

    def check(self, body, *argv, link=True):
        """The tool's --check on `body`, with a last line `Part of #61.`
        unless `link` is false (TicketLink)."""
        (self.tmp / 'body.md').write_text(body + ('\nPart of #61.\n' if link else ''))
        return self.run_tool('--check', str(self.tmp / 'body.md'), *argv)


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

    def test_a_run_not_ended_is_no_evidence(self):
        """#347's draft read "STILL RUNNING, the line is filled in when the
        detached run ends", and passed (#347 retro)."""
        for rest in ('STILL RUNNING, the line is filled in when the detached run ends',
                     'running at 9ae2c6a4', 'Pending.', 'queued on the load lock',
                     'in progress', 'Waiting on the load lock'):
            with self.subTest(rest=rest):
                body = evidence(KEYS[1:]) + f'- {KEYS[0].rstrip(".")}: {rest}\n'
                done = self.check(body)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn('no evidence in the body: Embedded images', done.stdout)
        for rest in ('25 of 25 passed (the run took 40 min)', 'runs at 17fd0994 passed',
                     'queue-free run: 25 passed'):
            with self.subTest(rest=rest):
                body = evidence(KEYS[1:]) + f'- {KEYS[0].rstrip(".")}: {rest}\n'
                done = self.check(body)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

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


HEAD = '19fea1fb0000000000000000000000000000beef'
RUN = 'https://github.com/PorkChopExpress86/derbynet/actions/runs/'
# The fields pr-evidence reads, as the runs API answers for a ci-load run.
CI_LOAD_RUN = {'path': '.github/workflows/ci-load.yml', 'event': 'workflow_dispatch',
               'head_branch': 'master', 'run_attempt': 1,
               'display_title': f'CI load at {HEAD}', 'status': 'completed',
               'conclusion': 'success'}
PR_HEAD = '0123456789abcdef0123456789abcdef01234567'


class CiLoad(IssueStandIn, unittest.TestCase):
    """A `- CI load: <run URL>` item, read through a `gh` that also answers
    the runs API from runs.json and PR 421's head (#415)."""

    def setUp(self):
        super().setUp()
        (self.tmp / 'runs.json').write_text('{}')
        gh = self.tmp / 'gh'
        gh.write_text(f'#!{sys.executable}\n'
                      'import json, sys\n'
                      'if sys.argv[1] == "api":\n'
                      f'    runs = json.load(open({str(self.tmp / "runs.json")!r}))\n'
                      '    run = runs.get(sys.argv[2].rsplit("/", 1)[1])\n'
                      '    if run is None:\n'
                      '        sys.exit("HTTP 404: Not Found")\n'
                      '    print(json.dumps(run))\n'
                      '    sys.exit(0)\n'
                      'if sys.argv[1:3] == ["pr", "view"]:\n'
                      '    if sys.argv[3] != "421":\n'
                      '        sys.exit("no pull requests found")\n'
                      f'    print(json.dumps({{"headRefOid": {PR_HEAD!r}}}))\n'
                      '    sys.exit(0)\n'
                      'assert sys.argv[1:4] == ["issue", "view", "61"], sys.argv\n'
                      f'print(open({str(self.tmp / "issue.md")!r}).read(), end="")\n')

    def runs(self, **by_id):
        (self.tmp / 'runs.json').write_text(json.dumps(by_id))

    def check_at(self, items, head=HEAD, pr=None, flake_ticket=False):
        (self.tmp / 'body.md').write_text('Part of #61.\n\n' + evidence(KEYS) + items)
        argv = ['--check', str(self.tmp / 'body.md')] + (['--head', head] if head else [])
        return self.run_tool(*argv, *(['--pr', pr] if pr else []),
                             *(['--flake-ticket'] if flake_ticket else []))

    def test_a_passed_run_at_the_head_is_accepted_as_ci_load(self):
        self.runs(**{'101': CI_LOAD_RUN})
        done = self.check_at(f'- CI load: {RUN}101\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f'CI load at {HEAD}, passed: {RUN}101 (extra evidence until the '
                      'owner amends D85', done.stdout)

    def test_each_wrong_field_is_refused_with_its_reason(self):
        cases = {'path': ('.github/workflows/server.yml', 'not a run of'),
                 'event': ('push', 'started by push'),
                 'head_branch': ('issue-415-ci-load-runs',
                                 "ran the workflow of issue-415-ci-load-runs, not master's"),
                 'run_attempt': (2, 'attempt 2, not the first'),
                 'display_title': ('CI load at ' + '0' * 40, "is not at the PR's head"),
                 'conclusion': ('failure', 'completed, failure: not passed'),
                 'status': ('in_progress', 'in_progress, success: not passed')}
        for field, (value, why) in cases.items():
            with self.subTest(field=field):
                self.runs(**{'102': {**CI_LOAD_RUN, field: value}})
                done = self.check_at(f'- CI load: {RUN}102\n')
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn('not load evidence, ', done.stdout)
                self.assertIn(why, done.stdout)

    def test_no_head_no_url_or_another_repo_is_refused(self):
        self.runs(**{'103': CI_LOAD_RUN})
        for items, head, why in (
                (f'- CI load: {RUN}103\n', None, 'no --head or --pr to check it at'),
                ('- CI load: the run below\n', HEAD, 'no run URL'),
                ('- CI load: https://github.com/jeffpiazza/derbynet/actions/runs/103\n',
                 HEAD, 'a run of jeffpiazza/derbynet')):
            with self.subTest(why=why):
                done = self.check_at(items, head)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn(why, done.stdout)

    def test_a_trial_at_the_head_is_refused(self):
        """The workflow names a run with any non-default input a trial."""
        trial = f'CI load trial at {HEAD} (files x.py, base origin/master, flake ticket false)'
        self.runs(**{'107': {**CI_LOAD_RUN, 'display_title': trial}})
        done = self.check_at(f'- CI load: {RUN}107\n')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"not load evidence, {trial!r} is not at the PR's head {HEAD}",
                      done.stdout)

    def test_a_flake_ticket_run_at_the_head_is_accepted_for_any_ticket(self):
        """D101 and D122 ask a flake ticket for its whole-file repeats with
        --flake-ticket; the workflow names that run `(flake ticket)`, and a
        ticket not said to be a flake ticket takes it too."""
        title = f'CI load at {HEAD} (flake ticket)'
        self.runs(**{'109': {**CI_LOAD_RUN, 'display_title': title}})
        for flake_ticket in (False, True):
            with self.subTest(flake_ticket=flake_ticket):
                done = self.check_at(f'- CI load: {RUN}109\n', flake_ticket=flake_ticket)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f'{title}, passed: {RUN}109 (extra evidence until the owner '
                              'amends D85', done.stdout)

    def test_a_plain_run_is_refused_for_a_flake_ticket_with_the_reason(self):
        self.runs(**{'110': CI_LOAD_RUN})
        done = self.check_at(f'- CI load: {RUN}110\n', flake_ticket=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"not load evidence, 'CI load at {HEAD}' ran without flake_ticket, and "
                      'this ticket is a flake ticket: D101 asks its whole-file 25 with '
                      f'--flake-ticket (D122): {RUN}110', done.stdout)

    def test_a_flake_ticket_run_must_still_be_at_the_head_and_no_trial(self):
        """The `(flake ticket)` name opens no other door: the head, the
        trial name and every other field are checked as for a plain run, and
        the verdict is the same whether or not the ticket is a flake ticket."""
        trial = f'CI load trial at {HEAD} (files x.py; base origin/master; flake ticket true)'
        flake = f'CI load at {HEAD} (flake ticket)'
        for fields, why in (
                ({'display_title': f'CI load at {"0" * 40} (flake ticket)'}, "is not at the PR's head"),
                ({'display_title': trial}, "is not at the PR's head"),
                ({'display_title': flake + ' '}, "is not at the PR's head"),
                ({'display_title': flake, 'run_attempt': 2}, 'attempt 2, not the first'),
                ({'display_title': flake, 'conclusion': 'failure'}, 'completed, failure: not passed'),
                ({'display_title': flake, 'status': 'in_progress'}, 'in_progress, success: not passed')):
            for flake_ticket in (False, True):
                with self.subTest(fields=fields, flake_ticket=flake_ticket):
                    verdict = TOOL.ci_load_verdict({**CI_LOAD_RUN, **fields}, HEAD, flake_ticket)
                    self.assertIn(why, verdict)

    def test_the_verdict_names_the_run_each_kind_of_ticket_takes(self):
        flake = f'CI load at {HEAD} (flake ticket)'
        for title, flake_ticket, verdict in (
                (f'CI load at {HEAD}', False, None),
                (flake, False, None),
                (flake, True, None),
                (f'CI load at {HEAD}', True, 'ran without flake_ticket')):
            with self.subTest(title=title, flake_ticket=flake_ticket):
                got = TOOL.ci_load_verdict({**CI_LOAD_RUN, 'display_title': title}, HEAD,
                                           flake_ticket)
                self.assertTrue(got is None if verdict is None else verdict in got, got)

    def test_pr_gives_the_head_and_a_head_that_is_not_the_prs_is_refused(self):
        self.runs(**{'108': {**CI_LOAD_RUN, 'display_title': f'CI load at {PR_HEAD}'}})
        for head in (None, PR_HEAD):
            with self.subTest(head=head):
                done = self.check_at(f'- CI load: {RUN}108\n', head=head, pr='421')
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f'CI load at {PR_HEAD}, passed: {RUN}108', done.stdout)
        done = self.check_at(f'- CI load: {RUN}108\n', head=HEAD, pr='421')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'--head {HEAD} is not the head of PR #421, {PR_HEAD}', done.stderr)

    def test_a_pr_gh_cannot_read_exits_with_its_message(self):
        done = self.check_at('', head=None, pr='999')
        self.assertEqual(done.returncode, 1)
        self.assertIn('gh could not read PR #999:\nno pull requests found', done.stderr)

    def test_head_or_pr_without_check_prints_the_usage(self):
        for option, value in (('--head', [HEAD]), ('--pr', ['421']), ('--flake-ticket', [])):
            with self.subTest(option=option):
                done = self.run_tool(option, *value)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn('--head, --pr, --flake-ticket and --closes go with --check', done.stderr)
                self.assertIn('Usage: plan/bin/pr-evidence', done.stderr)

    def test_other_run_links_and_still_open_items_are_not_read(self):
        """This PR's own body cites a failing run for its failing-shard box."""
        done = self.check_at(f'- A failing shard: {RUN}104 failed, as it should\n'
                             f'\n## Still open\n\n- CI load: {RUN}105 waits on D85\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('CI load', done.stdout)

    def test_a_misspelt_head_option_prints_the_usage(self):
        (self.tmp / 'body.md').write_text(evidence(KEYS))
        done = self.run_tool('--check', str(self.tmp / 'body.md'), '--hed', HEAD)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('Usage: plan/bin/pr-evidence', done.stderr)

    def test_a_run_gh_cannot_read_exits_with_its_message(self):
        done = self.check_at(f'- CI load: {RUN}106\n')
        self.assertEqual(done.returncode, 1)
        self.assertIn(f'gh could not read {RUN}106:\nHTTP 404', done.stderr)


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
        # A name joined by _ that server/ or testing/ assigns is a code name:
        # the #408 review found VIEW_RACE_RESULTS (a permission in server/).
        # From merged bodies: #355's OUTPUT_CURL, #185's MAX_RUNS_PER_LANE,
        # #333's HEAD_SHA. Each is looked up in this checkout by git grep.
        body = ('OUTPUT_CURL and MAX_RUNS_PER_LANE, VIEW_RACE_RESULTS_PERMISSION, HEAD_SHA,\n'
                'The VIEW_RACE_RESULTS permission, see "VIEW_RACE_RESULTS".\n'
                'HAS_RESULT, RENDER_RACER_RESULTS, PURGE_RESULTS, ROUND_TABLE, DEFAULT_OUTPUT,\n'
                'TIMER_TEST_RESULT ok, `HANDOFF_RUNS` in code,\n'
                '`X-RESULT` in code, $LOOP_OUTPUT, ${HANDOFF-RUNS}, path/LOOP-OUTPUT,\n'
                'LOOP-OUTPUTS, TABLE, OUTPUT, LOOP-OUTPUT.txt, LOOP-OUTPUT/x, DRY-RUN,\n'
                '$X-OUTPUT, x.X-OUTPUT, a-X-OUTPUT, LOOP-OUTPUT-FILE, a_X_OUTPUT, x.X_OUTPUT\n')
        self.assertEqual(TOOL.placeholders(body), [])

    def test_406s_handoff_runs_is_found_in_the_real_tree(self):
        # #406's draft and #399's held HANDOFF_RUNS; no file under server/
        # or testing/ assigns it.
        body = 'The hand-off ran HANDOFF_RUNS at the head.\nVIEW_RACE_RESULTS stays.\n'
        self.assertEqual(TOOL.placeholders(body), ['line 1: HANDOFF_RUNS'])

    def test_each_joined_placeholder_from_the_drafts_is_found(self):
        # Tokens from #399's, #407's and retro-65's drafts, one or more for
        # each word of SUFFIXES.
        tokens = ('HANDOFF_RUNS', 'HANDOFF_RESULTS', 'CLAIMS_TABLE', 'HEAD_PENDING',
                  'LISTED_RESULT', 'LOAD_RUN_PENDING', 'LOAD_RUN_RESULT', 'MUTANT_TABLE',
                  'MUTATIONS_PENDING', 'MUTATION_RESULT', 'PROBE_PENDING', 'NOTES_TBD',
                  'X_OUTPUT', 'HANDOFF_TODO')
        self.assertEqual({t.rsplit('_', 1)[1] for t in tokens}, set(TOOL.SUFFIXES))
        for token in tokens:
            for text in (f'| R13 | none | ({token}). |', f'**{token}**', f'{token}:'):
                with self.subTest(text=text):
                    self.assertEqual(TOOL.placeholders(text, defined=lambda t: set()),
                                     [f'line 1: {token}'])

    def test_a_joined_token_the_tree_names_passes_and_one_it_lacks_is_kept(self):
        asked = []

        def defined(tokens):
            asked.append(tokens)
            return {'HAS_RESULT'}

        body = 'HAS_RESULT and LOOP_OUTPUT, then LOOP-OUTPUT.\nMAX_RUNS_PER_LANE, $X_OUTPUT\n'
        self.assertEqual(TOOL.placeholders(body, defined=defined),
                         ['line 1: LOOP_OUTPUT', 'line 1: LOOP-OUTPUT'])
        self.assertEqual(asked, [{'HAS_RESULT', 'LOOP_OUTPUT'}])

    def test_defined_names_reads_the_checkout_without_a_hooks_git_dir(self):
        # Under a pre-push hook GIT_DIR names the main clone's git folder;
        # defined_names drops it and reads its own checkout.
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {'GIT_DIR': str(Path(tmp) / 'nowhere')}):
            self.assertEqual(TOOL.defined_names({'VIEW_RACE_RESULTS', 'HANDOFF_RUNS'}),
                             {'VIEW_RACE_RESULTS'})

    def test_defined_names_keeps_the_environment_but_git_s_own(self):
        # git keeps PATH and HOME (its config) and loses only GIT_*.
        seen = []

        def run(argv, **kwargs):
            seen.append(kwargs['env'])
            return subprocess.CompletedProcess(argv, 1, '', '')

        with unittest.mock.patch.dict(os.environ, {'GIT_DIR': '/nowhere', 'HOME': '/home/x'}), \
                unittest.mock.patch.object(TOOL.subprocess, 'run', run):
            TOOL.defined_names({'HANDOFF_RUNS'})
        self.assertEqual(seen[0]['HOME'], '/home/x')
        self.assertNotIn('GIT_DIR', seen[0])

    def test_defined_names_with_no_match_is_empty(self):
        # git grep exits 1 when it finds none of the tokens.
        self.assertEqual(TOOL.defined_names({'HANDOFF_RUNS'}), set())

    def test_defined_names_runs_no_git_for_no_tokens(self):
        with unittest.mock.patch.object(TOOL.subprocess, 'run', side_effect=AssertionError):
            self.assertEqual(TOOL.defined_names(set()), set())

    def test_a_tree_git_cannot_read_exits_with_its_message(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(TOOL, 'ROOT', tmp):
            with self.assertRaises(SystemExit) as stop:
                TOOL.defined_names({'HANDOFF_RUNS'})
        self.assertIn('pr-evidence: git grep could not read the tree', str(stop.exception))

    def test_the_shapes_it_leaves_out(self):
        # The #408 review's probes the rule does not take: a placeholder that
        # is no capitalised token joined by -.
        for text in ('<LOOP OUTPUT>', '<<PENDING>>', 'TBD', 'TODO: fill', 'XXX',
                     '(paste output here)', 'Loop-Output', 'loop-output'):
            with self.subTest(text=text):
                self.assertEqual(TOOL.placeholders(text), [])


class DefinedNames(unittest.TestCase):
    """defined_names against a small repository, since the rule is which
    files and which lines count."""

    FILES = {
        'plan/README.md': '| `bin/x` | HANDOFF_RUNS |\nHANDOFF_RUNS = 1\n',
        'plan/bin/tool': 'PLAN_ONLY_RESULT = 1\n',
        'server/vendor.js': 'var LOG_TABLE = [];\n',
        'server/notes.md': 'EXP_TABLE is a table\n',
        'server/a.py': (
            'TIMER_TEST_RESULT = re.compile("tt")\n'
            'class Purge:\n'
            '    PURGE_RESULTS = 90\n'
            '# COMMENT_RESULT = 1\n'
            '    SAME_RESULT == 1\n'
            'x = INLINE_RESULT = 2\n'
            'def f(): return DEF_OUTPUT\n'
        ),
        'testing/c.sh': 'export OUTPUT_CURL=/x\nreadonly HEAD_SHA=\nCI_OUTPUT=$(x)\n',
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'repo'
        # Under a hook GIT_DIR names another repository.
        patched = unittest.mock.patch.dict(os.environ, {'GIT_DIR': str(Path(tmp.name) / 'hook.git')})
        patched.start()
        self.addCleanup(patched.stop)
        for name, text in self.FILES.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        env = dict(os.environ)
        del env['GIT_DIR']
        for argv in (['init', '-q'], ['add', '.']):
            subprocess.run(['git', '-C', str(self.root), *argv], check=True, env=env)

    def defined(self, *tokens):
        with unittest.mock.patch.object(TOOL, 'ROOT', str(self.root)):
            return TOOL.defined_names(set(tokens))

    def test_a_name_a_file_under_server_or_testing_assigns_is_defined(self):
        names = ('TIMER_TEST_RESULT', 'PURGE_RESULTS', 'OUTPUT_CURL', 'HEAD_SHA', 'CI_OUTPUT')
        self.assertEqual(self.defined(*names), set(names))

    def test_a_name_only_a_longer_name_is_assigned_as_is_not_defined(self):
        # TEST_RESULT ends TIMER_TEST_RESULT, PURGE_RESULT starts
        # PURGE_RESULTS, and OUTPUT_CUR starts OUTPUT_CURL.
        self.assertEqual(self.defined('TEST_RESULT', 'PURGE_RESULT', 'OUTPUT_CUR'), set())

    def test_a_name_only_plan_mentions_or_assigns_is_not_defined(self):
        # plan/README.md quotes HANDOFF_RUNS, as a row once did.
        self.assertEqual(self.defined('HANDOFF_RUNS', 'PLAN_ONLY_RESULT'), set())

    def test_a_mention_that_is_no_assignment_is_not_defined(self):
        # Prose, a comment, a comparison, a use, a name `x = NAME = 2`
        # assigns second, and a name JavaScript declares with `var`.
        names = ('EXP_TABLE', 'COMMENT_RESULT', 'SAME_RESULT', 'DEF_OUTPUT',
                 'INLINE_RESULT', 'LOG_TABLE')
        self.assertEqual(self.defined(*names), set())

    def test_a_placeholder_these_files_do_not_assign_is_refused_end_to_end(self):
        body = 'HANDOFF_RUNS, TEST_RESULT, TIMER_TEST_RESULT, PURGE_RESULTS, LOG_TABLE\n'
        with unittest.mock.patch.object(TOOL, 'ROOT', str(self.root)):
            found = TOOL.placeholders(body)
        self.assertEqual(found, ['line 1: HANDOFF_RUNS', 'line 1: TEST_RESULT',
                                 'line 1: LOG_TABLE'])


class CheckRefuses(IssueStandIn, unittest.TestCase):
    def test_399s_draft_is_refused_for_its_placeholder(self):
        done = self.check(evidence(KEYS) + '\n' + DRAFT_399, '--closes', '399')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:],
                         ['unfilled placeholder, line 17: LOOP-OUTPUT'])

    def test_406s_joined_placeholder_is_refused(self):
        body = evidence(KEYS) + '\n## Hand-off\n\nHANDOFF_RUNS\n'
        line = body.splitlines().index('HANDOFF_RUNS') + 1
        done = self.check(body)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:],
                         [f'unfilled placeholder, line {line}: HANDOFF_RUNS'])

    def test_the_draft_with_its_output_filled_in_passes(self):
        # Its "Closes #399." above its "## Still open" passes: the lead
        # dropped that check (AGENT-BRIEF puts undecided divergences there).
        filled = DRAFT_399.replace('LOOP-OUTPUT', '25 of 25 ok')
        done = self.check(evidence(KEYS) + '\n' + filled, '--closes', '399')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_body_with_crlf_line_ends_is_refused_for_its_placeholder(self):
        # The #408 review's crlf.md, as bytes.
        (self.tmp / 'body.md').write_bytes(
            (evidence(KEYS) + 'x\n- Manual check\nLOOP-OUTPUT\nPart of #61.\n').replace('\n', '\r\n').encode())
        done = self.run_tool('--check', str(self.tmp / 'body.md'))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:],
                         ['unfilled placeholder, line 11: LOOP-OUTPUT'])


# The lines of #444's body that named #434, which its merge left open (#434
# retro), with #434 as the stand-in's #61.
BODY_444 = ('## Why\n\n`test_kiosk_results_by_racer.py`\'s give-up test should pass every '
            'run, on CI and under load (#61).\n\n## What changed\n\nThe test only.\n')
UNLINKED = ('no "Closes #61" or "Part of #61" outside code: the merge leaves #61 open '
            'with no link to the PR (an HTML comment, <pre>, <code>, a ~~~ fence and an '
            'indented line count as code)')


class TicketLink(IssueStandIn, unittest.TestCase):
    """A body names #61 in a closing keyword or "Part of #61". The cases call
    `TOOL.links_ticket` in process (plan/AGENT-BRIEF.md); the command line,
    for the exit code and the message, runs only in
    `test_444s_body_naming_the_ticket_in_prose_is_refused` and
    `test_a_body_with_no_issue_needs_no_link`."""

    def test_444s_body_naming_the_ticket_in_prose_is_refused(self):
        done = self.check(evidence(KEYS) + BODY_444, link=False)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:], [UNLINKED])

    def test_each_link_outside_code_passes(self):
        for line in ('Closes #61.', 'Fixes: #61', 'resolves #61', 'Part of #61.',
                     'This is part of #61, as its Still open says.',
                     'Closes PorkChopExpress86/derbynet#61',
                     'Closes https://github.com/PorkChopExpress86/derbynet/issues/61'):
            with self.subTest(line=line):
                self.assertTrue(TOOL.links_ticket(BODY_444 + line + '\n', '61'))

    def test_a_link_in_code_another_number_or_repo_or_a_bare_mention_is_refused(self):
        for line in ('`Closes #61`', '```\nPart of #61\n```', 'Part of #610', 'Part of #6',
                     'Part of `#61`', 'Refs #61', 'Issue #61.', 'counterpart of #61',
                     'Closes jeffpiazza/derbynet#61', 'Closes #610', 'Fixes #59',
                     'Closes https://github.com/jeffpiazza/derbynet/issues/61'):
            with self.subTest(line=line):
                self.assertFalse(TOOL.links_ticket(BODY_444 + line + '\n', '61'))

    def test_a_link_github_draws_as_code_or_not_at_all_is_refused(self):
        """`gh api markdown` linked #61 in none of these (an HTML comment, a
        ~~~ fence, an indented block, a fence with no end, <pre> and <code>,
        a quoted fence); the PR body pastes the runs."""
        for lines in ('<!-- Part of #61 -->', 'a <!-- Part of #61 --> b',
                      '<!--\nPart of #61\n-->', '<!--\nxxPart of #61\nxx\n-->', '<!--\nCloses #61',
                      'a <!-- Part of #61\ny --> b', '<pre>\nxxCloses #61\n</pre>',
                      '~~~\nPart of #61\n~~~', '~~~~\nCloses #61\n~~~~', '~~~\nCloses #61',
                      '```\nPart of #61', '    Part of #61', '\tCloses #61',
                      '- item\n\n      Part of #61',
                      '~~~\nx\n~~~ y\nPart of #61\n~~~', '~~~\n```\nPart of #61\n```\n~~~',
                      '```\n~~~\nPart of #61\n~~~\n```',
                      '<pre>Part of #61</pre>', '<PRE>\nPart of #61\n</PRE>',
                      '<code>Part of #61</code>', 'x <pre>Part of #61', 'x <code>Part of #61',
                      '> ```\n> Part of #61\n> ```', '> quote\n>\n>     Part of #61'):
            with self.subTest(lines=lines):
                self.assertFalse(TOOL.links_ticket(BODY_444 + lines + '\n', '61'))

    def test_a_link_after_such_code_ends_passes(self):
        """`gh api markdown` linked #61 in each of these too."""
        for lines in ('<!-- note -->\nPart of #61', '<!-- note --> Part of #61',
                      '<!--\nnote\n--> Closes #61', '~~~\nx\n~~~\nPart of #61',
                      '~~~~\n~~~\nx\n~~~~\nPart of #61', '~~~\nx\n~~~~\nPart of #61',
                      '   Part of #61', '- Part of #61', '<!--\n```\n-->\nPart of #61',
                      '<!-- a --> Part of #61 <!-- b -->', 'a <!-- x\ny --> Part of #61',
                      '<pre>x</pre> Part of #61', '<code>x</code>\nPart of #61',
                      '<precious>Part of #61</precious>', '<script>Part of #61</script>',
                      '> Part of #61', '> ```\n> x\n> ```\n\nPart of #61'):
            with self.subTest(lines=lines):
                self.assertTrue(TOOL.links_ticket(BODY_444 + lines + '\n', '61'))

    def test_the_limit_left_is_a_false_refusal_never_a_missed_one(self):
        """`gh api markdown` linked #61 in each of these, and the strict
        reading refuses them: it leaves out a lazy continuation line, which
        it cannot tell from a code block without parsing the paragraph above,
        and a comment opened mid-line and never closed."""
        for lines in ('text\n    Part of #61', 'x <!-- Closes #61'):
            with self.subTest(lines=lines):
                self.assertFalse(TOOL.links_ticket(BODY_444 + lines + '\n', '61'))

    def test_a_close_foreign_closes_must_refuse_is_still_read_in_that_code(self):
        """The default view keeps what GitHub might act on: only a closed
        backtick fence and a code span are quoted."""
        for lines in ('<!-- Closes #59 -->', '~~~\nCloses #59\n~~~', '    Closes #59',
                      '```\nCloses #59', '> ```\n> Closes #59\n> ```'):
            with self.subTest(lines=lines):
                self.assertEqual(len(TOOL.foreign_closes(lines + '\n', {'61'})), 1)

    def test_still_open_alone_is_no_link(self):
        body = evidence(KEYS[1:]) + '\n## Still open\n\n- ' + KEYS[0] + '\n'
        self.assertFalse(TOOL.links_ticket(body, '61'))
        self.assertTrue(TOOL.links_ticket(body + 'Part of #61.\n', '61'))

    def test_a_body_with_no_issue_needs_no_link(self):
        (self.tmp / 'body.md').write_text('## Summary\n\nA retro.\n')
        done = subprocess.run([sys.executable, '-I', str(PR_EVIDENCE), '--check',
                               str(self.tmp / 'body.md')],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, NO_ISSUE + '\n', ''))


# The line of #421's body draft that pr-merge refused at merge time, since
# GitHub read it as closing #399 (#415 retro), and the line as it merged.
LINE_421 = ('- `test_pr_evidence.py::CheckRefuses` "Closes #399." and "## Still open": '
            'DRAFT_399 holds both lines, and the class\'s tests check that draft.')
MERGED_421 = LINE_421.replace('"Closes #399."', '"`Closes #399.`"').replace(
    '"## Still open"', '"`## Still open`"')
NO_ISSUE = "no issue given: no ticket's evidence lines were checked"
CLOSES_421 = ('closes another issue, line {n}: Closes #399: GitHub closes it on merge; '
              'put a quote in backticks, or write "Part of #N"')


class ClosingKeywords(IssueStandIn, unittest.TestCase):
    """A closing keyword for an issue other than the PR's own, outside code."""

    def body(self, *lines):
        """The #61 stand-in's evidence, `Closes #61.` and `lines`, and the
        number of the first of `lines`."""
        head = 'Closes #61.\n\n' + evidence(KEYS) + '\n'
        return head + ''.join(f'{line}\n' for line in lines), head.count('\n') + 1

    def test_421s_quoted_fixture_line_is_refused(self):
        body, n = self.body(LINE_421)
        done = self.check(body)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:], [CLOSES_421.format(n=n)])

    def test_421s_line_as_it_merged_passes(self):
        body, _n = self.body(MERGED_421)
        done = self.check(body)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[1:], [])

    def test_the_prs_own_issue_and_each_closes_option_pass(self):
        body, _n = self.body('Fixes #64 C5 (Q3), as #404 did.', 'Resolves: #65')
        done = self.check(body, '--closes', '64', '--closes', '65')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        done = self.check(body, '--closes', '64')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual([line.split(':')[0] for line in done.stdout.splitlines()[1:]],
                         [f'closes another issue, line {_n + 1}'])

    def test_each_form_github_reads_is_found(self):
        for words in ('close #59', 'closes #59', 'closed #59', 'fix #59', 'fixes #59',
                      'fixed #59', 'resolve #59', 'resolves #59', 'resolved #59',
                      'CLOSES #59', 'Closes: #59', 'Closes\t#59', 'Closes owner/repo#59',
                      'Closes https://github.com/PorkChopExpress86/derbynet/issues/59',
                      '(closes #59)', 'see "Fixes #59."', 'closes#59', 'closes-#59',
                      'closes #59a', 'closes #59-b', 'closes - #59', '-fixes #59'):
            with self.subTest(words=words):
                found = TOOL.foreign_closes(f'Text {words} here.\n', {'61'})
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith('line 1: '), found)
                self.assertTrue(found[0].endswith('59'), found)

    def test_the_shapes_that_close_nothing_pass(self):
        for words in ('disclosed #59', 'prefixes #59', 'closes the #59 thread', 'closes #61',
                      'closes #61a', 'see #59', 'Closes `#59`', '`Closes #59`', 'Part of #59'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'Text {words} here.\n', {'61'}), [])

    def test_a_longer_number_is_another_issue(self):
        self.assertEqual(TOOL.foreign_closes('Closes #610.\nCloses #6.\n', {'61'}),
                         ['line 1: Closes #610', 'line 2: Closes #6'])

    def test_a_closed_fence_is_quoted_and_an_open_one_is_read(self):
        closed = 'Before.\n```\nCloses #59\n```\nAfter.\n'
        self.assertEqual(TOOL.foreign_closes(closed, {'61'}), [])
        indented = '- a log:\n\n  ```\n  Fixes #59\n  ```\n'
        self.assertEqual(TOOL.foreign_closes(indented, {'61'}), [])
        unclosed = 'Closes #58.\n```\nFixes #59\nCloses #57\n'
        self.assertEqual(TOOL.foreign_closes(unclosed, {'61'}),
                         ['line 1: Closes #58', 'line 3: Fixes #59', 'line 4: Closes #57'])
        reopened = '```\nCloses #59\n```\nFixes #58\n```\nCloses #57\n'
        self.assertEqual(TOOL.foreign_closes(reopened, {'61'}),
                         ['line 4: Fixes #58', 'line 6: Closes #57'])

    def test_the_gh_prefix_is_read_as_an_issue_reference(self):
        """GitHub's autolinked references include GH-N; whether its closing
        keywords honour that form is unverified, so it fails closed."""
        for words in ('Closes GH-59', 'closes gh-59', 'Fixes: GH-59', 'resolved GH-59'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'Text {words}.\n', {'61'}),
                                 [f'line 1: {words}'])
        self.assertEqual(TOOL.foreign_closes('Closes GH-61.\n', {'61'}), [])
        self.assertEqual(TOOL.foreign_closes('Closes `GH-59`, see GH-59.\n', {'61'}), [])

    def test_a_fence_opener_has_no_backtick_in_its_info_string(self):
        """CommonMark: "```code``` here" is inline code, not a fence, so the
        prose after it is read."""
        for line in ('```code``` here', '```Closes #58``` here', '````code``` here'):
            with self.subTest(line=line):
                self.assertEqual(TOOL.foreign_closes(f'{line}\nFixes #59\n```\nAfter.\n', {'61'}),
                                 ['line 2: Fixes #59'])
        self.assertEqual(TOOL.foreign_closes('```Closes #58``` here\n', {'61'}), [])
        self.assertEqual(TOOL.foreign_closes('```python\nFixes #59\n```\n', {'61'}), [])

    def test_only_a_bare_fence_at_least_as_long_closes_one(self):
        inside = '```\n```code``` x\nCloses #59\n```\nAfter.\n'
        self.assertEqual(TOOL.foreign_closes(inside, {'61'}), [])
        longer = '````\n```\nCloses #59\n````\nFixes #58\n'
        self.assertEqual(TOOL.foreign_closes(longer, {'61'}), ['line 5: Fixes #58'])
        titled = '```\n```python\nCloses #59\n```\nAfter.\n'
        self.assertEqual(TOOL.foreign_closes(titled, {'61'}), [])
        trailing = '```\nCloses #59\n``` \nFixes #58\n'
        self.assertEqual(TOOL.foreign_closes(trailing, {'61'}), ['line 4: Fixes #58'])

    def test_a_code_span_ends_at_the_backticks_that_opened_it(self):
        for words in ('``Closes #59``', '``Closes `x` #59``', 'Closes `x` #59',
                      'Clo`x`ses #59', '```Closes #59```', '`` Closes #59 ``'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'Text {words} here.\n', {'61'}), [])
        self.assertEqual(TOOL.foreign_closes('`x` Closes #59 `y`\n', {'61'}),
                         ['line 1: Closes #59'])
        self.assertEqual(TOOL.foreign_closes('``x` Closes #59\n', {'61'}),
                         ['line 1: Closes #59'])

    def test_backtick_runs_of_different_lengths_are_no_span(self):
        for words in ('` Closes #59 ``', '`` Closes #59 `', '```` Closes #59 ``'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'{words}\n', {'61'}),
                                 ['line 1: Closes #59'])

    def test_a_reference_into_another_repo_is_refused_whatever_its_number(self):
        for words in ('Closes other/repo#61', 'Closes jeffpiazza/derbynet#61',
                      'Fixes https://github.com/jeffpiazza/derbynet/issues/61'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'{words}\n', {'61'}),
                                 [f'line 1: {words}'])
        for words in ('Closes PorkChopExpress86/derbynet#61', 'closes porkchopexpress86/DerbyNet#61',
                      'Fixes https://github.com/PorkChopExpress86/derbynet/issues/61'):
            with self.subTest(words=words):
                self.assertEqual(TOOL.foreign_closes(f'{words}\n', {'61'}), [])
        self.assertEqual(TOOL.foreign_closes('Closes PorkChopExpress86/derbynet#59\n', {'61'}),
                         ['line 1: Closes PorkChopExpress86/derbynet#59'])

    def test_closes_takes_a_positive_issue_number(self):
        body = evidence(KEYS) + '\nFixes #64.\n'
        for given in ('64', '#64'):
            with self.subTest(given=given):
                done = self.check(body, '--closes', given)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for given in ('abc', '0', '064', '6 4', '', '##64', '-1', '64a', '#'):
            with self.subTest(given=given):
                done = self.check(body, '--closes', given)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(done.stdout, '')
                self.assertIn(f'argument --closes: takes an issue number such as 64 or #64, not {given!r}',
                              done.stderr)

    def test_a_prs_number_is_refused_too(self):
        """The limit left: #377's "The fake gh's closed #325" names a PR,
        which GitHub does not close."""
        line = "- The fake gh's closed #325 makes the claim, and the run gives 0 findings."
        self.assertEqual(TOOL.foreign_closes(line, {'346'}), ['line 1: closed #325'])

    def test_a_body_with_no_issue_refuses_any_close(self):
        (self.tmp / 'body.md').write_text('## Summary\n\nA retro.\n\n' + LINE_421 + '\n')
        done = subprocess.run([sys.executable, '-I', str(PR_EVIDENCE), '--check',
                               str(self.tmp / 'body.md')],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(), [NO_ISSUE, CLOSES_421.format(n=5)])
        (self.tmp / 'body.md').write_text('## Summary\n\nA retro.\n\n' + MERGED_421 + '\n')
        done = subprocess.run([sys.executable, '-I', str(PR_EVIDENCE), '--check',
                               str(self.tmp / 'body.md'), '--closes', '61'],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, NO_ISSUE + '\n', ''))

    def test_no_issue_and_no_check_or_closes_without_check_prints_the_usage(self):
        for argv in ([], ['61', '--closes', '64']):
            with self.subTest(argv=argv):
                done = subprocess.run([sys.executable, '-I', str(PR_EVIDENCE), *argv],
                                      capture_output=True, text=True, env=self.env)
                self.assertEqual(done.returncode, 1)
                self.assertIn('Usage: plan/bin/pr-evidence', done.stderr)


if __name__ == '__main__':
    unittest.main()
