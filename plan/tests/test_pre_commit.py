""".githooks/pre-commit in a scratch repository: it refuses a commit while
tools/mutate.py holds a lock, and runs ruff on the staged Python under
server/, with the worktree's own ruff.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / '.githooks' / 'pre-commit'
RUFF = REPO / 'server' / '.venv' / 'bin' / 'ruff'
# #400's 53dffcf2: two docstring lines of ui/kiosks/views.py joined into one
# of 93 columns, which the pre-push hook's ruff refused (#400 retro).
LINE_400 = ('(kiosk-dashboard.js:261-263), and the repeats land on the page\'s body. '
            'After a save PHP gives')


class PreCommit(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env = {**env, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
                    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                    'DERBYNET_RUFF': str(RUFF)}
        self.git('init', '-q', '-b', 'master')
        hooks = self.root / '.githooks'
        hooks.mkdir()
        shutil.copy(HOOK, hooks / 'pre-commit')
        self.git('config', 'core.hooksPath', '.githooks')
        (self.root / 'server').mkdir()
        shutil.copy(REPO / 'server' / 'pyproject.toml', self.root / 'server')

    def git(self, *argv):
        return subprocess.run(['git', *argv], cwd=self.root, env=self.env,
                              capture_output=True, text=True)

    def commit(self, rel, text):
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(text)
        self.git('add', '.')
        return self.git('commit', '-q', '-m', 'x')

    @unittest.skipUnless(RUFF.exists(), 'no server/.venv: run uv sync in server/')
    def test_400s_long_docstring_line_is_refused(self):
        done = self.commit('server/views.py', f'"""Docs.\n\n{LINE_400}\n"""\n')
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('E501', done.stdout + done.stderr)

    @unittest.skipUnless(RUFF.exists(), 'no server/.venv: run uv sync in server/')
    def test_clean_python_commits(self):
        done = self.commit('server/views.py', '"""Docs."""\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    @unittest.skipUnless(RUFF.exists(), 'no server/.venv: run uv sync in server/')
    def test_a_file_ruff_s_config_excludes_commits(self):
        """pyproject.toml excludes generator_tables.py, as the pre-push
        hook's `ruff check` of the whole tree leaves it out."""
        rel = 'server/racing/engine/scheduling/generator_tables.py'
        done = self.commit(rel, f'X = 1  # {LINE_400}\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_with_no_ruff_it_commits_and_says_so(self):
        self.env['DERBYNET_RUFF'] = str(self.root / 'no-ruff')
        done = self.commit('server/views.py', f'"""{LINE_400}"""\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('ruff is left to the pre-push hook', done.stderr)

    def test_a_mutate_lock_refuses_the_commit_and_names_it(self):
        git_dir = self.git('rev-parse', '--absolute-git-dir').stdout.strip()
        lock = Path(git_dir) / 'mutate-0123456789abcdef.lock'
        lock.write_text(str(os.getpid()))
        done = self.commit('notes.md', 'x\n')
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f'{lock} (pid {os.getpid()}, running)', done.stderr)
        lock.unlink()
        done = self.git('commit', '-q', '-m', 'x')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
