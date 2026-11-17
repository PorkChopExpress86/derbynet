""".githooks/pre-push runs plan/tests once on every push, in a scratch
repository with one plan test, stand-ins for uv and check-paths.sh and the
real source-checks.sh, and refuses a push typed after a pytest run in one
command.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

HOOK = Path(__file__).resolve().parents[2] / '.githooks' / 'pre-push'
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
         local: str = 'head') -> subprocess.CompletedProcess:
    """Runs the hook for a push of one commit that changes only `changed`, a
    path or a tuple of paths, each written `text`, from a `bash -c` script
    that runs `shell` first when it is given. `remote` is the remote sha git
    sends: the base commit; 'orphan', a new branch with no common history
    (no base); or 'missing', a commit this clone lacks. `base_files` are
    committed in the base, `delete` is removed by the change, and `local`
    'deleted' pushes a deletion of the branch."""
    # A hook runs with GIT_DIR and the like set; the scratch repository
    # needs its own.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t',
               GIT_COMMITTER_EMAIL='t@t', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM='1')
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)

        def git(*argv):
            return subprocess.run(['git', *argv], cwd=repo, env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

        git('init', '-q', '-b', 'master')
        (repo / '.githooks').mkdir()
        shutil.copy(HOOK, repo / '.githooks' / 'pre-push')
        (repo / 'server' / 'tools').mkdir(parents=True)
        shutil.copy(SOURCE_CHECKS, repo / 'server' / 'tools' / 'source-checks.sh')
        (repo / 'server' / 'docs' / 'adr').mkdir(parents=True)
        (repo / 'server' / 'docs' / 'adr' / 'check-paths.sh').write_text(CHECK_PATHS)
        stubs = Path(tmp, 'stubs')
        stubs.mkdir()
        (stubs / 'uv').write_text(UV)
        (stubs / 'uv').chmod(0o755)
        env.update(PATH=f'{stubs}:{os.environ["PATH"]}', STUB_LOG=str(Path(tmp, 'ran')))
        (repo / 'plan' / 'tests').mkdir(parents=True)
        (repo / 'plan' / 'tests' / 'test_plan.py').write_text(plan_test)
        # The stand-ins' log and the plan test's bytecode, which the hook
        # would count as a change of the tree, as the clone's own
        # .gitignore files keep its bytecode out.
        (repo / '.gitignore').write_text('/ran\n__pycache__/\n')
        for name, body in (base_files or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(body)
        git('add', '.')
        git('commit', '-q', '-m', 'base')
        base = git('rev-parse', 'HEAD')
        if remote == 'orphan':
            git('checkout', '-q', '--orphan', 'b')
        for name in (changed,) if isinstance(changed, str) else changed:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for name in delete:
            git('rm', '-q', name)
        git('add', '.')
        git('commit', '-q', '-m', 'change')
        head = git('rev-parse', 'HEAD')
        hook = ['bash', '.githooks/pre-push', 'origin', 'url']
        if shell:
            # Not the script's last command, so bash forks the hook as its child.
            hook = ['bash', '-c', f'{shell}\nbash .githooks/pre-push origin url; exit $?']
        result = subprocess.run(
            hook, cwd=repo, env=env,
            input=f'refs/heads/b {head if local == "head" else "0" * 40} refs/heads/b '
                  f'{REMOTES.get(remote, base)}\n',
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
        result = push('docs/x.md', PASSING)
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
        result = push('docs/x.md', FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)

    def test_passing_plan_tests_let_a_push_outside_server_through(self):
        result = push('docs/x.md', PASSING)
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
        result = push('.github/workflows/server.yml', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('pre-push: server/docs/adr/check-paths.sh\n', result.stderr)
        self.assertEqual(result.ran, 'check-paths.sh --docs docs\nplan/tests\n')


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
        self.refused(push('docs/x.md', PASSING, ': uv run pytest -q; : git push'))

    def test_a_push_with_options_before_push_is_refused(self):
        self.refused(push('docs/x.md', PASSING,
                          ': uv run pytest -q; : git -c core.hooksPath=.githooks push'))

    def test_a_push_after_reading_a_background_runs_output_is_refused(self):
        """#282's push, from its session log, with the paths shortened: the
        `.output` file it printed held a red Postgres browser run."""
        self.refused(push('docs/x.md', PASSING,
                          'cat /tmp/x/tasks/bcz7l8rxe.output; tail -2 /tmp/x/browser-pg.txt; '
                          'cd . && git status --short; : git push -q -u origin issue-282 2>&1 '
                          '| tail -3; git log --oneline -2'))

    def test_a_push_after_reading_a_junit_file_is_refused(self):
        self.refused(push('docs/x.md', PASSING,
                          'grep -c failure var/load-runs/a/junit.xml; : git push'))

    def test_a_push_after_a_postgres_run_is_refused(self):
        self.refused(push('docs/x.md', PASSING,
                          ': tools/pytest-postgres.sh -m browser ui && : git push'))

    def test_the_bash_tools_eval_wrapper_is_read(self):
        """The Bash tool runs `... && eval '<command>' && pwd -P ...`, with
        each quote in the command written `'"'"'`."""
        command = ": uv run pytest -k 'x'; : git push"
        wrapped = "eval '" + command.replace("'", "'\"'\"'") + "' && :"
        self.refused(push('docs/x.md', PASSING, wrapped))

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
                result = push('docs/x.md', PASSING, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('pre-push: plan/tests', result.stderr)


class TreeChanged(unittest.TestCase):
    """#373's implementer edited network_changed.py while the hook's tests
    ran it, and the run left rerun pytests behind and a file named `--` in
    server/ (#373 retro). A plan test stands in for the edit."""

    CHANGED = 'pre-push: the working tree changed while the checks ran'

    def edit(self, line, passes=True):
        test = PASSING if passes else FAILING
        return test.replace(
            "class Plan", "from pathlib import Path\n"
            "REPO = Path(__file__).resolve().parents[2]\n\n\nclass Plan").replace(
            "log.write('plan/tests\\n')", f"log.write('plan/tests\\n')\n            {line}")

    def test_an_edit_of_a_tracked_file_fails_the_push(self):
        for path in ('docs/x.md', 'server/tools/parity_gate.py'):
            with self.subTest(path=path):
                result = push(path, self.edit(
                    f"(REPO / '{path}').write_text('edited\\n')"))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(self.CHANGED, result.stderr)
                self.assertIn(f' M {path}', result.stderr)
                self.assertNotIn('the plan test failed', result.stderr)

    def test_a_second_edit_of_a_changed_file_fails_the_push(self):
        result = push('docs/x.md', self.edit(
            "(REPO / 'docs/x.md').write_text('again\\n')"), shell='echo dirty > docs/x.md')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(self.CHANGED, result.stderr)

    def test_a_new_file_fails_the_push(self):
        result = push('docs/x.md', self.edit("(REPO / 'server' / '--').write_text('x')"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('?? server/--', result.stderr)
        self.assertNotIn('the plan test failed', result.stderr)

    def test_a_failing_check_keeps_its_status_and_names_the_change(self):
        result = push('docs/x.md', self.edit(
            "(REPO / 'docs/x.md').write_text('edited\\n')", passes=False))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)
        self.assertIn(self.CHANGED, result.stderr)

    def test_a_tree_left_alone_passes_dirty_or_clean(self):
        for shell in ('', 'echo dirty > docs/x.md'):
            with self.subTest(shell=shell):
                result = push('docs/x.md', PASSING, shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(self.CHANGED, result.stderr)


if __name__ == '__main__':
    unittest.main()
