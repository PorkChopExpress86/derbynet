""".githooks/pre-push runs plan/tests once on every push, in a scratch
repository with one plan test, stand-ins for uv and check-paths.sh and the
real source-checks.sh, and refuses a push typed after a pytest run in one
command.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

HOOK = Path(__file__).resolve().parents[2] / '.githooks' / 'pre-push'
PLAN_BIN = Path(__file__).resolve().parents[2] / 'plan' / 'bin'
SOURCE_CHECKS = Path(__file__).resolve().parents[2] / 'server' / 'tools' / 'source-checks.sh'
# The server checks' stand-ins: each notes its arguments and passes.
UV = '#!/bin/sh\necho "uv $*" >>"$STUB_LOG"\n'
# It notes its arguments, and fails the guides' check when a guide holds
# BROKEN, as #345's `manage.py` did, or cites a `server/` path that is gone.
CHECK_PATHS = '''echo "check-paths.sh $*" >>"$STUB_LOG"
if [ "$1" = --docs ]; then
  if grep -qs BROKEN docs/*.md; then echo "MISSING  manage.py"; exit 1; fi
  for p in $(grep -ohs '`server/[^`]*`' docs/*.md | tr -d '`'); do
    [ -e "$p" ] || { echo "MISSING  $p"; exit 1; }
  done
fi
echo "0 failures"
'''
FAILING = '''import os
import unittest


class Plan(unittest.TestCase):
    def test_it_fails(self):
        with open(os.environ['STUB_LOG'], 'a') as log:
            log.write('plan/tests\\n')
        self.fail('the plan test failed')
'''


def push(changed, plan_test: str, shell: str = '', text: str = 'changed\n',
         remote: str = 'base', base_files: dict | None = None, delete: tuple = (),
         local: str = 'head', git_dir: bool = False, message: str = 'change',
         environ: dict | None = None, base_message: str = 'base',
         origin: bool = False, other_clone: bool = False, pushed_before: bool = False,
         checked_out: str = 'head', ahead: str | None = None,
         ref: str = 'refs/heads/b', two_commits: bool = False) -> subprocess.CompletedProcess:
    """Runs the hook for a push of one commit that changes only `changed`, a
    path or a tuple of paths, each written `text`, from a `bash -c` script
    that runs `shell` first when it is given. `remote` is the remote sha git
    sends: the base commit; 'orphan', a new branch with no common history
    (no base); or 'missing', a commit this clone lacks. `base_files` are
    committed in the base, `delete` is removed by the change, and `local`
    'deleted' pushes a deletion of the branch. `message` is the change commit's
    message, `environ` adds variables to the hook's environment, `base_message` is
    the base commit's, `origin` makes the base commit origin/master, and
    `other_clone` runs the hook and tools of a second checkout, as
    core.hooksPath does, while the pushed tree holds none of the tools,
    `pushed_before` makes the pushed commit the head of a remote-tracking
    branch other than origin/master, and `checked_out` 'base' leaves the base
    commit checked out while the head is pushed. The change is committed on
    branch b, cut from master; `ahead` is the message of a commit on local
    master atop the base, as a pull leaves it when origin/master is stale,
    which b is cut from. `ref` is the ref pushed to. `two_commits` also
    pushes the base commit to refs/heads/m, so the checks read the working
    tree, not a copy of the pushed commit. The copy goes under the
    XDG_CACHE_HOME `environ` names, or a scratch folder."""
    # A hook runs with GIT_DIR and the like set; the scratch repository
    # needs its own.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
               GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM='1')
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as main:
        repo = Path(tmp)

        def git(*argv):
            return subprocess.run(['git', *argv], cwd=repo, env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

        git('init', '-q', '-b', 'master')
        clone = Path(main) if other_clone else repo
        (clone / '.githooks').mkdir(exist_ok=True)
        shutil.copy(HOOK, clone / '.githooks' / 'pre-push')
        (clone / 'plan' / 'bin').mkdir(parents=True, exist_ok=True)
        for tool in ('commit-closes', 'pr-evidence'):
            shutil.copy(PLAN_BIN / tool, clone / 'plan' / 'bin' / tool)
        (repo / 'plan' / 'bin').mkdir(parents=True, exist_ok=True)
        (repo / 'server' / 'tools').mkdir(parents=True)
        shutil.copy(SOURCE_CHECKS, repo / 'server' / 'tools' / 'source-checks.sh')
        (repo / 'server' / 'docs' / 'adr').mkdir(parents=True)
        (repo / 'server' / 'docs' / 'adr' / 'check-paths.sh').write_text(CHECK_PATHS)
        stubs = Path(tmp, 'stubs')
        stubs.mkdir()
        (stubs / 'uv').write_text(UV)
        (stubs / 'uv').chmod(0o755)
        env.update({'PATH': f'{stubs}:{os.environ["PATH"]}', 'STUB_LOG': str(Path(tmp, 'ran')),
                    'XDG_CACHE_HOME': str(Path(tmp, 'cache')), **(environ or {})})
        (repo / 'plan' / 'tests').mkdir(parents=True)
        (repo / 'plan' / 'tests' / 'test_plan.py').write_text(plan_test)
        # The stand-ins' log and the plan test's bytecode, which the hook
        # would count as a change of the tree, as the clone's own
        # .gitignore files keep its bytecode out.
        (repo / '.gitignore').write_text('/ran\n/cache/\n__pycache__/\n')
        for name, body in (base_files or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        git('add', '.')
        git('commit', '-q', '-m', base_message)
        base = git('rev-parse', 'HEAD')
        if origin:
            git('update-ref', 'refs/remotes/origin/master', base)
        if ahead is not None:
            (repo / 'ahead.txt').write_text('ahead\n')
            git('add', '.')
            git('commit', '-q', '-m', ahead)
        git('checkout', '-q', *(('--orphan',) if remote == 'orphan' else ('-b',)), 'b')
        for name in (changed,) if isinstance(changed, str) else changed:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for name in delete:
            git('rm', '-q', name)
        git('add', '.')
        git('commit', '-q', '-m', message)
        head = git('rev-parse', 'HEAD')
        if pushed_before:
            git('update-ref', 'refs/remotes/origin/b', head)
        if checked_out == 'base':
            git('checkout', '-q', '--detach', base)
        hook_file = clone / '.githooks' / 'pre-push'
        hook = ['bash', str(hook_file), 'origin', 'url']
        if shell:
            # Not the script's last command, so bash forks the hook as its child.
            hook = ['bash', '-c', f'{shell}\nbash {hook_file} origin url; exit $?']
        # Git runs a hook with GIT_DIR set to the pushing checkout's.
        hook_env = {**env, 'GIT_DIR': str(repo / '.git')} if git_dir else env
        result = subprocess.run(
            hook, cwd=repo, env=hook_env,
            input=f'refs/heads/b {head if local == "head" else "0" * 40} {ref} '
                  f'{REMOTES.get(remote, base)}\n'
                  + (f'refs/heads/m {base} refs/heads/m {"0" * 40}\n' if two_commits else ''),
            capture_output=True, text=True)
        ran = Path(tmp, 'ran')
        result.ran = ran.read_text() if ran.exists() else ''
        return result


class PrePush(unittest.TestCase):
    def test_a_failing_plan_test_stops_a_push_of_the_parity_gate(self):
        """#222 changed server/tools/parity_gate.py, and only plan-checks.yml
        found the plan test it broke (#218 retro)."""
        result = push('server/tools/parity_gate.py', FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)

    def test_a_push_of_server_runs_plan_tests_once(self):
        """source-checks.sh runs plan/tests, and #311's push ran them a
        second time after the hook's own run (#311 retro)."""
        result = push('server/tools/parity_gate.py', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran.count('plan/tests'), 1, result.ran)
        self.assertIn('uv run --locked ruff check', result.ran)
        self.assertIn('pytest -q -p no:cacheprovider', result.ran)

    def test_a_push_outside_server_runs_plan_tests_once(self):
        result = push('notes/x.md', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran, 'check-paths.sh --docs docs\nplan/tests\n')

    def test_a_push_of_plan_runs_the_source_checks_that_read_it(self):
        """test_registry.py and test_deviations.py read owner-decisions.md,
        test_listing_order.py plan/'s Python, test_docker_checks.py every
        tracked .md, and test_pipefail_pipes.py plan/bin's bash; #346's push
        ran none of them (#346 retro, D129)."""
        for path in ('plan/owner-decisions.md', 'plan/php_cites.py',
                     'plan/bin/ci-failures', 'plan/README.md'):
            with self.subTest(path=path):
                result = push(path, PASSING)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('pre-push: tools/source-checks.sh', result.stderr)
                self.assertEqual(result.ran.count('plan/tests'), 1, result.ran)

    def test_a_failing_plan_test_stops_a_push_outside_server(self):
        result = push('notes/x.md', FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)

    def test_passing_plan_tests_let_a_push_outside_server_through(self):
        result = push('notes/x.md', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)


class ImageWorkflow(unittest.TestCase):
    """image.yml runs checks.sh, which server/tests/test_docker_checks.py
    reads, and server.yml does not run on an image.yml-only push, so the hook
    runs its server checks for it (#58 retro)."""

    def test_an_image_yml_push_runs_the_server_checks(self):
        result = push('.github/workflows/image.yml', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Not `check-paths.sh` alone: the guides' check, run on every push,
        # prints that too, and a hook without image.yml passed (#382 review
        # round's mutant).
        self.assertIn('pre-push: tools/source-checks.sh', result.stderr)

    def test_another_workflow_push_runs_only_plan_tests(self):
        """And the guides' check, which runs on every push (Guides)."""
        result = push('.github/workflows/plan-checks.yml', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('pre-push: server/docs/adr/check-paths.sh\n', result.stderr)
        self.assertEqual(result.ran, 'check-paths.sh --docs docs\nplan/tests\n')


class GuideCommands(unittest.TestCase):
    """server/tests/test_guide_commands.py reads docs/*.md and the workflows
    that run guide-commands.sh's stages, so a push that changes only such a
    file runs the source checks that hold it (#414 review: a push of a guide
    and nothing else ran none)."""

    def test_a_guide_or_a_stage_workflow_push_runs_the_source_checks(self):
        for path in ('docs/pi-setup.md', '.github/workflows/server.yml',
                     '.github/workflows/pi-images.yml'):
            with self.subTest(path=path):
                result = push(path, PASSING)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('pre-push: tools/source-checks.sh', result.stderr)

    def test_a_guide_image_or_a_nested_page_runs_no_source_checks(self):
        for path in ('docs/img/x.png', 'docs/tools/x.md'):
            with self.subTest(path=path):
                result = push(path, PASSING)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('pre-push: tools/source-checks.sh', result.stderr)


PASSING = FAILING.replace("self.fail('the plan test failed')", 'pass')
REMOTES = {'orphan': '0' * 40, 'missing': '1' * 40}


class Guides(unittest.TestCase):
    """#345's guide sentence named `manage.py` in backticks, which
    check-paths.sh --docs reads as a missing top-level path. The hook ran
    check-paths.sh on the ADRs alone, so the push passed and the hand-off
    failed, and a new head cost a second push and a restart (#345 retro).
    plan-checks.yml runs the guides' check, so the hook runs it on every
    push of a branch: a guide can cite any path, and PR #364's review moved
    a cited `server/accounts/permissions.py` past a hook that ran it only
    for a push that changed a guide."""

    def test_a_guide_push_runs_the_guides_check(self):
        result = push('docs/install-docker.md', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check-paths.sh --docs docs\n', result.ran)
        self.assertEqual(result.ran.count('plan/tests'), 1, result.ran)

    def test_a_broken_guide_stops_a_push_outside_server(self):
        result = push('docs/install-docker.md', PASSING, text='BROKEN\n')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('MISSING  manage.py', result.stdout)

    def test_a_broken_guide_stops_a_push_of_server_too(self):
        """#345's own push, f7c0f3cf, changed server/manage.py beside the
        guides."""
        result = push(('server/manage.py', 'docs/database.md'), PASSING, text='BROKEN\n')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('MISSING  manage.py', result.stdout)
        self.assertIn('check-paths.sh --docs docs\n', result.ran)

    def test_a_move_of_a_cited_path_stops_the_push(self):
        """PR #364's review: `git mv server/accounts/permissions.py`, which
        a guide cites, changes no guide."""
        cited = 'server/accounts/permissions.py'
        result = push('server/accounts/moved.py', PASSING, delete=(cited,),
                      base_files={'docs/roles.md': f'See `{cited}`.\n', cited: 'x\n'})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f'MISSING  {cited}', result.stdout)

    def test_a_branch_deletion_alone_runs_no_guides_check(self):
        result = push('plan/README.md', PASSING, local='deleted')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran, 'plan/tests\n')

    def test_a_push_with_no_base_runs_every_check(self):
        """A new branch with no history in common with master has no base:
        both checks run, and the hook does not die on `git diff` with an
        empty base (the #345 retro's first hook did)."""
        result = push('plan/README.md', PASSING, remote='orphan')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check-paths.sh --docs docs\n', result.ran)
        self.assertIn('uv run --locked ruff check', result.ran)

    def test_a_base_the_clone_lacks_runs_the_guides_check_and_plan_tests(self):
        """The diff lists nothing, so the server checks are skipped, as
        before the guides' check."""
        result = push('docs/install-docker.md', PASSING, remote='missing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran, 'check-paths.sh --docs docs\nplan/tests\n')

    def test_every_branch_push_runs_the_guides_check(self):
        for changed in ('plan/README.md', 'server/tools/parity_gate.py', 'server/docs/adr/README.md'):
            with self.subTest(changed=changed):
                result = push(changed, PASSING)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('check-paths.sh --docs docs\n', result.ran)


class ChainedPush(unittest.TestCase):
    """#238 chained `git push` after the full suite, and #282 after printing
    a red run's output, and each pushed a red head (#282 retro). The hook reads every
    shell above it, so these fail when the command that runs them names a
    pytest run before a push, which the hook would refuse anyway. Each
    pushes a docs/ file, whose let-through push runs plan/tests alone: a
    push that changes plan/ runs the source checks (D129)."""

    def refused(self, result):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('runs in the same command as a pytest run', result.stderr)
        self.assertNotIn('pre-push: plan/tests', result.stderr)

    def test_a_push_after_a_pytest_run_in_one_command_is_refused(self):
        self.refused(push('notes/x.md', PASSING, ': uv run pytest -q; : git push'))

    def test_a_push_with_options_before_push_is_refused(self):
        self.refused(push('notes/x.md', PASSING,
                          ': uv run pytest -q; : git -c core.hooksPath=.githooks push'))

    def test_a_push_after_reading_a_background_runs_output_is_refused(self):
        """#282's push, from its session log, with the paths shortened: the
        `.output` file it printed held a red Postgres browser run."""
        self.refused(push('notes/x.md', PASSING,
                          'cat /tmp/x/tasks/bcz7l8rxe.output; tail -2 /tmp/x/browser-pg.txt; '
                          'cd . && git status --short; : git push -q -u origin issue-282 2>&1 '
                          '| tail -3; git log --oneline -2'))

    def test_a_push_after_reading_a_junit_file_is_refused(self):
        self.refused(push('notes/x.md', PASSING,
                          'grep -c failure var/load-runs/a/junit.xml; : git push'))

    def test_a_push_after_a_postgres_run_is_refused(self):
        self.refused(push('notes/x.md', PASSING,
                          ': tools/pytest-postgres.sh -m browser ui && : git push'))

    def test_the_bash_tools_eval_wrapper_is_read(self):
        """The Bash tool runs `... && eval '<command>' && pwd -P ...`, with
        each quote in the command written `'"'"'`."""
        command = ": uv run pytest -k 'x'; : git push"
        wrapped = "eval '" + command.replace("'", "'\"'\"'") + "' && :"
        self.refused(push('notes/x.md', PASSING, wrapped))

    def test_pytest_in_a_message_a_comment_or_after_the_push_is_let_through(self):
        for shell in (': git commit -m "uv run pytest passed" && : git push',
                      ": git commit -F - <<'MSG'\nuv run pytest passed\nMSG\n: git push",
                      ": git commit -m 'uv run pytest passed' && : git push",
                      '# uv run pytest earlier\n: git push',
                      ': git push; : uv run pytest',
                      # A shell that names no push, such as one running a
                      # test that pushes to a scratch repository.
                      ': uv run pytest -q'):
            with self.subTest(shell=shell):
                result = push('notes/x.md', PASSING, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('pre-push: plan/tests', result.stderr)


class TreeChanged(unittest.TestCase):
    """#373's implementer edited network_changed.py while the hook's tests
    ran it, and the run left rerun pytests behind and a file named `--` in
    server/ (#373 retro). A plan test stands in for the edit. A push of one
    commit checks a copy of it (TreeCopy), so these push two."""

    CHANGED = 'pre-push: the working tree changed while the checks ran'

    def edit(self, line, passes=True):
        test = PASSING if passes else FAILING
        return test.replace(
            "class Plan", "from pathlib import Path\n"
            "REPO = Path(__file__).resolve().parents[2]\n\n\nclass Plan").replace(
            "log.write('plan/tests\\n')", f"log.write('plan/tests\\n')\n            {line}")

    def test_an_edit_of_a_tracked_file_fails_the_push(self):
        for path in ('notes/x.md', 'server/tools/parity_gate.py'):
            with self.subTest(path=path):
                result = push(path, two_commits=True, plan_test=self.edit(
                    f"(REPO / '{path}').write_text('edited\\n')"))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(self.CHANGED, result.stderr)
                self.assertIn(f' M {path}', result.stderr)
                self.assertNotIn('the plan test failed', result.stderr)

    def test_a_second_edit_of_a_changed_file_fails_the_push(self):
        result = push('notes/x.md', two_commits=True, plan_test=self.edit(
            "(REPO / 'notes/x.md').write_text('again\\n')"), shell='echo dirty > notes/x.md')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(self.CHANGED, result.stderr)

    def test_a_new_file_fails_the_push(self):
        result = push('notes/x.md', two_commits=True,
                      plan_test=self.edit("(REPO / 'server' / '--').write_text('x')"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('?? server/--', result.stderr)
        self.assertNotIn('the plan test failed', result.stderr)

    def test_an_edit_of_a_file_untracked_at_the_start_fails_the_push(self):
        """`git diff HEAD` skips untracked files and `status` prints
        `?? path` before and after, so the hook compares their contents too
        (#390 review)."""
        result = push('notes/x.md', two_commits=True, plan_test=self.edit(
            "(REPO / 'notes/new.md').write_text('again\\n')"), shell='echo first > notes/new.md')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(self.CHANGED, result.stderr)
        self.assertIn('?? notes/new.md', result.stderr)
        self.assertNotIn('the plan test failed', result.stderr)

    def test_a_failing_check_keeps_its_status_and_names_the_change(self):
        result = push('notes/x.md', two_commits=True, plan_test=self.edit(
            "(REPO / 'notes/x.md').write_text('edited\\n')", passes=False))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)
        self.assertIn(self.CHANGED, result.stderr)

    def test_a_tree_left_alone_passes_dirty_or_clean(self):
        for shell in ('', 'echo dirty > notes/x.md', 'echo first > notes/new.md'):
            with self.subTest(shell=shell):
                result = push('notes/x.md', two_commits=True, plan_test=PASSING, shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(self.CHANGED, result.stderr)


class TreeCopy(unittest.TestCase):
    """A push of one commit runs its checks in a detached copy of it, so the
    working tree is free while they run: #400's implementer edited it during
    the hook, which refused the push about 25 min late, and #470's sat idle
    through six pushes (#470 retro). The plan test runs in the copy; the main
    checkout is the first worktree git lists."""

    CHANGED = TreeChanged.CHANGED
    edit = TreeChanged.edit
    MAIN = ("import subprocess\n            MAIN = Path(subprocess.run(['git', '-C', "
            "str(REPO), 'worktree', 'list', '--porcelain'], capture_output=True, "
            "text=True).stdout.split()[1])\n            ")

    def test_an_edit_of_the_working_tree_during_the_checks_passes(self):
        result = push('notes/x.md', self.edit(
            self.MAIN + "(MAIN / 'notes/x.md').write_text('edited\\n')"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.CHANGED, result.stderr)
        self.assertIn('pre-push: the checks run in ', result.stderr)

    def test_the_checks_read_the_pushed_commit_not_the_working_tree(self):
        line = "assert (REPO / 'notes/x.md').read_text() == 'changed\\n', 'read the tree'"
        result = push('notes/x.md', self.edit(line), shell='echo dirty > notes/x.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = push('notes/x.md', self.edit(line.replace("'changed", "'dirty")),
                      shell='echo dirty > notes/x.md')
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_a_commit_on_the_pushed_ref_during_the_checks_fails_the_push(self):
        """The #470 retro's push checked a9d8d04e, and GitHub got 50650764,
        committed during the checks."""
        result = push('notes/x.md', self.edit(
            self.MAIN + "subprocess.run(['git', '-C', str(MAIN), 'commit', '-q', "
            "'--allow-empty', '-m', 'during'], check=True)"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('pre-push: refs/heads/b moved from ', result.stderr)

    def test_the_copy_is_removed_after_a_pass_or_a_failure(self):
        for passes in (True, False):
            with self.subTest(passes=passes), tempfile.TemporaryDirectory() as cache:
                result = push('notes/x.md', self.edit('pass', passes=passes),
                              environ={'XDG_CACHE_HOME': cache})
                self.assertEqual(result.returncode, 0 if passes else 1, result.stderr)
                self.assertIn(f'pre-push: the checks run in {cache}/derbynet-pre-push-',
                              result.stderr)
                self.assertEqual(os.listdir(cache), [])


HANGING = """import os
import signal
import time
import unittest
from pathlib import Path


class Plan(unittest.TestCase):
    def test_it_hangs_when_asked(self):
        if 'HANG_PID' not in os.environ:
            return
        if os.environ.get('HANG_IGNORE_TERM'):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path(os.environ['HANG_PID']).write_text(str(os.getpid()))
        time.sleep(120)
"""


class CopySignals(unittest.TestCase):
    """The checks of a one-commit push run in a copy, as a process group the
    trap stops before it removes the copy. A second signal during that
    cleanup, and a SIGKILL followed by the next push, each leave no copy and
    no check running (AGENT-BRIEF, PR #492 review)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.cache = self.tmp / 'repo', self.tmp / 'cache'
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
                   GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM='1', XDG_CACHE_HOME=str(self.cache),
                   STUB_LOG=str(self.tmp / 'ran'), PRE_PUSH_KILL_WAIT='2')
        self.env = env
        repo = self.repo
        (repo / '.githooks').mkdir(parents=True)
        shutil.copy(HOOK, repo / '.githooks' / 'pre-push')
        (repo / 'plan' / 'bin').mkdir(parents=True)
        for tool in ('commit-closes', 'pr-evidence'):
            shutil.copy(PLAN_BIN / tool, repo / 'plan' / 'bin' / tool)
        (repo / 'plan' / 'tests').mkdir()
        (repo / 'plan' / 'tests' / 'test_plan.py').write_text(HANGING)
        (repo / 'server' / 'docs' / 'adr').mkdir(parents=True)
        (repo / 'server' / 'docs' / 'adr' / 'check-paths.sh').write_text(CHECK_PATHS)
        (repo / '.gitignore').write_text('__pycache__/\n')
        self.git('init', '-q', '-b', 'master')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        (repo / 'notes').mkdir()
        (repo / 'notes' / 'x.md').write_text('changed\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'change')
        self.head = self.git('rev-parse', 'HEAD')

    def git(self, *argv):
        return subprocess.run(['git', *argv], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def start(self, **environ):
        hook = subprocess.Popen(
            ['bash', str(self.repo / '.githooks' / 'pre-push'), 'origin', 'url'],
            cwd=self.repo, env={**self.env, **environ}, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)
        hook.stdin.write(f'refs/heads/master {self.head} refs/heads/b {self.base}\n')
        hook.stdin.close()
        return hook

    def hanging(self, **environ):
        """The hook, once its plan test hangs in the copy, and that test's pid."""
        pid_file = self.tmp / 'hang.pid'
        hook = self.start(HANG_PID=str(pid_file), **environ)
        for _ in range(600):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.1)
        else:
            hook.kill()
            self.fail(hook.communicate()[1])
        pid = int(pid_file.read_text())
        self.addCleanup(self.kill, pid)
        return hook, pid

    @staticmethod
    def kill(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def running(pid):
        """Whether `pid` runs, waiting up to 5 s for it to end."""
        for _ in range(50):
            try:
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                return False
            if state == 'Z':
                return False
            time.sleep(0.1)
        return True

    def assert_no_copy(self):
        self.assertEqual(list(self.cache.glob('derbynet-pre-push-*')), [])
        self.assertEqual(self.git('worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_a_wait_that_is_no_whole_number_stops_the_push(self):
        hook = self.start(PRE_PUSH_KILL_WAIT='2s')
        _, err = hook.communicate(timeout=60)
        self.assertEqual(hook.returncode, 1, err)
        self.assertIn("PRE_PUSH_KILL_WAIT takes a whole number of seconds, not '2s'", err)

    def test_a_term_stops_the_checks_and_removes_the_copy(self):
        hook, pid = self.hanging()
        hook.send_signal(signal.SIGTERM)
        _, err = hook.communicate(timeout=60)
        self.assertEqual(hook.returncode, 143, err)
        self.assertFalse(self.running(pid), 'the check outlived the hook')
        self.assert_no_copy()

    def test_a_second_signal_during_the_cleanup_still_removes_the_copy(self):
        hook, pid = self.hanging(HANG_IGNORE_TERM='1')
        hook.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        hook.send_signal(signal.SIGTERM)
        _, err = hook.communicate(timeout=60)
        self.assertEqual(hook.returncode, 143, err)
        self.assertFalse(self.running(pid), 'the check outlived the hook')
        self.assert_no_copy()

    def test_the_next_push_removes_a_killed_hooks_copy_and_its_checks(self):
        hook, pid = self.hanging()
        hook.kill()
        # The check it left holds the hook's pipes open.
        hook.wait(timeout=60)
        hook.stdout.close()
        hook.stderr.close()
        copy = self.cache / f'derbynet-pre-push-{hook.pid}'
        self.assertTrue(copy.is_dir())
        self.assertTrue(self.running(pid))
        again = self.start()
        _, err = again.communicate(timeout=120)
        self.assertEqual(again.returncode, 0, err)
        self.assertIn(f'pre-push: removed {copy}, left by pre-push {hook.pid}, which is gone',
                      err)
        self.assertFalse(self.running(pid), 'the killed hook\'s check still runs')
        self.assert_no_copy()


class CommitClosingKeyword(unittest.TestCase):
    """A closing keyword in a commit message closes its issue when the
    commit reaches master. #415's first commit, 73a1c975, quoted
    `Closes #399.` and the agent caught it by hand; the hook now refuses it
    before git sends the commit, and before any slow check (#439). Each
    pushes notes/x.md, whose let-through push runs plan/tests alone, and
    test_commit_closes.py holds the matcher's cases."""

    def refused(self, result, *words):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('closes another issue', result.stdout)
        for word in words:
            self.assertIn(word, result.stdout)
        self.assertIn('refused:', result.stderr)
        self.assertEqual(result.ran, '', 'a refusal comes before every other check')

    def test_a_closing_keyword_in_a_message_is_refused_before_every_check(self):
        message = 'A subject\n\nA body line\nthat says Closes #399.'
        self.refused(push('notes/x.md', PASSING, message=message), 'line 4: Closes #399')

    def test_a_quoted_keyword_passes(self):
        result = push('notes/x.md', PASSING, message='Quotes `Closes #399.` and see #399')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran, 'check-paths.sh --docs docs\nplan/tests\n')

    def test_the_issues_the_push_closes_on_purpose_split_on_blanks(self):
        message = 'Closes #399, fixes #64, resolves #5.'
        result = push('notes/x.md', PASSING, message=message,
                      environ={'DERBYNET_PUSH_CLOSES': '#64\t399'})
        self.refused(result, 'line 1: resolves #5')
        self.assertEqual(result.stdout.count('closes another issue'), 1, result.stdout)

    def test_a_bad_name_in_the_variable_stops_the_push(self):
        result = push('notes/x.md', PASSING, message='plain',
                      environ={'DERBYNET_PUSH_CLOSES': '399x'})
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('takes an issue number', result.stderr)
        self.assertEqual(result.ran, '')

    def test_a_commit_on_origin_master_is_not_read(self):
        """Master's own history has upstream's `Fixes #N` commits, which
        closed upstream's issues long ago."""
        result = push('notes/x.md', PASSING, base_message='Fixes #1', origin=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_commit_on_local_master_is_not_read_when_origin_master_is_stale(self):
        """A pull that leaves origin/master behind local master: master's own
        `Closes #316.` (4f7e0dff on the fork) came with the branch, and the
        push of the branch passes (#439 review)."""
        result = push('notes/x.md', PASSING, origin=True, ahead='Upstream\n\nCloses #316.')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_keyword_on_the_branch_atop_a_stale_origin_master_is_refused(self):
        result = push('notes/x.md', PASSING, origin=True, ahead='Clean', message='Fixes #2')
        self.refused(result, 'Fixes #2')

    def test_a_push_to_master_reads_local_master(self):
        """Local master is the commit pushed, so it is no base."""
        result = push('notes/x.md', PASSING, origin=True, ahead='Upstream\n\nCloses #316.',
                      ref='refs/heads/master')
        self.refused(result, 'Closes #316')

    def test_the_ticket_a_branch_names_may_be_closed(self):
        """pr-evidence --check lets the ticket's own keyword through in a PR
        body; the hook takes the ticket from `issue-<n>-` in the pushed ref."""
        result = push('notes/x.md', PASSING, origin=True, message='Closes #439.',
                      ref='refs/heads/issue-439-commit-closing-keyword')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_ref_that_names_no_ticket_closes_none(self):
        for ref, words in (('refs/heads/issue-439', 'Closes #439'),
                           ('refs/heads/issue-0439-x', 'Closes #439'),
                           ('refs/heads/b', 'Closes #439'),
                           ('refs/heads/x/refs/heads/issue-439-y', 'Closes #439'),
                           ('refs/heads/issue-4390-x', 'Closes #439'),
                           ('refs/heads/issue-439-x', 'Closes owner/repo#439'),
                           ('refs/heads/issue-439-x', 'Closes #43')):
            with self.subTest(ref=ref, words=words):
                result = push('notes/x.md', PASSING, origin=True, message=words, ref=ref)
                self.refused(result, words)

    def test_a_commit_no_remote_has_is_read_when_there_is_no_origin_master(self):
        self.refused(push('notes/x.md', PASSING, base_message='Fixes #1'), 'Fixes #1')

    def test_a_commit_already_pushed_on_another_branch_is_read_again(self):
        """It reaches master with the PR's squash, so a branch pushed twice is
        read twice."""
        result = push('notes/x.md', PASSING, message='Fixes #1', origin=True,
                      pushed_before=True)
        self.refused(result, 'Fixes #1')

    def test_the_pushed_commit_is_read_not_the_checked_out_one(self):
        result = push('notes/x.md', PASSING, message='Fixes #1', origin=True,
                      checked_out='base')
        self.refused(result, 'Fixes #1')

    def test_a_push_that_changes_server_is_refused_before_the_slow_checks_too(self):
        result = push('server/tools/parity_gate.py', PASSING, message='Fixes #1')
        self.refused(result, 'Fixes #1')

    def test_a_branch_deletion_reads_no_commit(self):
        result = push('notes/x.md', PASSING, message='Fixes #1', local='deleted')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_missing_tool_stops_the_push(self):
        result = push('notes/x.md', PASSING, shell='rm plan/bin/commit-closes')
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.ran, '')

    def test_the_tool_is_the_one_beside_the_hook_not_the_pushed_trees(self):
        """core.hooksPath names the main clone's .githooks, so a branch cut
        before the tool existed is pushed through a hook whose tree lacks it."""
        result = push('notes/x.md', PASSING, other_clone=True, message='Fixes #1')
        self.refused(result, 'Fixes #1')
        result = push('notes/x.md', PASSING, other_clone=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class GitDirUnset(unittest.TestCase):
    """Git runs the hook with GIT_DIR set to the pushing checkout's git
    directory. Under it, a plan test's `git init` in a scratch folder
    reinitializes that repository instead: pushed from a worktree, it set
    core.bare=true in the main clone's .git/config, and every checkout
    failed "must be run in a work tree" for about 3 min, from this retro's
    own first test_pr_wait.py (#351 and #369 retro). The checks find the
    repository from the working tree."""

    def test_the_checks_run_with_no_git_dir(self):
        result = push('notes/x.md', TreeChanged.edit(
            None, "log.write(f'GIT_DIR={os.environ.get(\"GIT_DIR\")}\\n')"), git_dir=True,
            shell='echo "before the hook: GIT_DIR=$GIT_DIR" >>"$STUB_LOG"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.ran, r'before the hook: GIT_DIR=/\S+/\.git\n')
        self.assertIn('GIT_DIR=None\n', result.ran)


if __name__ == '__main__':
    unittest.main()
