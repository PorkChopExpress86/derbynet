""".githooks/pre-push runs plan/tests, in a scratch repository whose only
test fails, so it runs no server check.

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


def push(changed: str, plan_test: str) -> subprocess.CompletedProcess:
    """Runs the hook for a push of one commit that changes only `changed`."""
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
        return subprocess.run(
            ['bash', '.githooks/pre-push', 'origin', 'url'], cwd=repo, env=env,
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
        result = push('plan/README.md', FAILING.replace("self.fail('the plan test failed')", 'pass'))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
