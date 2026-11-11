""".githooks/pre-push runs plan/tests, in a scratch repository whose only
test fails, so it runs no server check, and refuses a push typed after a
pytest run in one command.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / '.githooks' / 'pre-push'
FAILING = '''import unittest


class Plan(unittest.TestCase):
    def test_it_fails(self):
        self.fail('the plan test failed')
'''


def push(changed: str, plan_test: str, shell: str = '') -> subprocess.CompletedProcess:
    """Runs the hook for a push of one commit that changes only `changed`,
    from a `bash -c` script that runs `shell` first when it is given."""
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
        (repo / 'plan' / 'tests').mkdir(parents=True)
        (repo / 'plan' / 'tests' / 'test_plan.py').write_text(plan_test)
        git('add', '.')
        git('commit', '-q', '-m', 'base')
        base = git('rev-parse', 'HEAD')
        path = repo / changed
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('changed\n')
        git('add', '.')
        git('commit', '-q', '-m', 'change')
        head = git('rev-parse', 'HEAD')
        hook = ['bash', '.githooks/pre-push', 'origin', 'url']
        if shell:
            # Not the script's last command, so bash forks the hook as its child.
            hook = ['bash', '-c', f'{shell}\nbash .githooks/pre-push origin url; exit $?']
        return subprocess.run(
            hook, cwd=repo, env=env,
            input=f'refs/heads/b {head} refs/heads/b {base}\n',
            capture_output=True, text=True)


class PrePush(unittest.TestCase):
    def test_a_failing_plan_test_stops_a_push_of_the_parity_gate(self):
        """#222 changed server/tools/parity_gate.py, and only plan-checks.yml
        found the plan test it broke (#218 retro)."""
        # A failing plan test stops the hook before it reaches server/.
        result = push('server/tools/parity_gate.py', FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)

    def test_a_failing_plan_test_stops_a_push_outside_server(self):
        result = push('plan/README.md', FAILING)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('the plan test failed', result.stderr)

    def test_passing_plan_tests_let_a_push_outside_server_through(self):
        result = push('plan/README.md', PASSING)
        self.assertEqual(result.returncode, 0, result.stderr)


PASSING = FAILING.replace("self.fail('the plan test failed')", 'pass')


class ChainedPush(unittest.TestCase):
    """#238 chained `git push` after the full suite, and #282 after printing
    a red run's output, and each pushed a red head (#282 retro). The hook reads every
    shell above it, so these fail when the command that runs them names a
    pytest run before a push, which the hook would refuse anyway."""

    def refused(self, result):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('runs in the same command as a pytest run', result.stderr)
        self.assertNotIn('pre-push: plan/tests', result.stderr)

    def test_a_push_after_a_pytest_run_in_one_command_is_refused(self):
        self.refused(push('plan/README.md', PASSING, ': uv run pytest -q; : git push'))

    def test_a_push_with_options_before_push_is_refused(self):
        self.refused(push('plan/README.md', PASSING,
                          ': uv run pytest -q; : git -c core.hooksPath=.githooks push'))

    def test_a_push_after_reading_a_background_runs_output_is_refused(self):
        """#282's push, from its session log, with the paths shortened: the
        `.output` file it printed held a red Postgres browser run."""
        self.refused(push('plan/README.md', PASSING,
                          'cat /tmp/x/tasks/bcz7l8rxe.output; tail -2 /tmp/x/browser-pg.txt; '
                          'cd . && git status --short; : git push -q -u origin issue-282 2>&1 '
                          '| tail -3; git log --oneline -2'))

    def test_a_push_after_reading_a_junit_file_is_refused(self):
        self.refused(push('plan/README.md', PASSING,
                          'grep -c failure var/load-runs/a/junit.xml; : git push'))

    def test_a_push_after_a_postgres_run_is_refused(self):
        self.refused(push('plan/README.md', PASSING,
                          ': tools/pytest-postgres.sh -m browser ui && : git push'))

    def test_the_bash_tools_eval_wrapper_is_read(self):
        """The Bash tool runs `... && eval '<command>' && pwd -P ...`, with
        each quote in the command written `'"'"'`."""
        command = ": uv run pytest -k 'x'; : git push"
        wrapped = "eval '" + command.replace("'", "'\"'\"'") + "' && :"
        self.refused(push('plan/README.md', PASSING, wrapped))

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
                result = push('plan/README.md', PASSING, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('pre-push: plan/tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
