"""Each full commit sha a skill under .agents/skills names, such as
heat-intermission's sync commit, is a commit on origin/master.

pr-merge squashes, so a branch commit a skill names is not on master once
its PR merges: #503's squash left heat-intermission's sync commit at
3b4d1a32, a commit of the issue-499 branch, where master holds the same code
as 7c3cd879 (#499 retro). A ticket PR that changes the model leaves the sha
as it is, and a follow-up names the squash commit after the merge (the
skill's SKILL.md), so the check passes on a ticket PR and fails on a branch
sha.

origin/master is the branch as this clone last fetched it: a follow-up whose
squash commit is newer than the fetch fails until `git fetch origin`.

Run: python3 -I -m unittest discover -s plan/tests
"""
import contextlib
import os
import re
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r'(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])')


@contextlib.contextmanager
def no_git_vars():
    """os.environ without its GIT_* variables while the block runs: a hook's
    or a caller's GIT_DIR names another repository than the tree, as in
    plan/bin/affected-tests (#431)."""
    hidden = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith('GIT_')}
    try:
        yield
    finally:
        os.environ.update(hidden)


def git(root, *argv) -> subprocess.CompletedProcess:
    with no_git_vars():
        return subprocess.run(['git', '-C', str(root), *argv], capture_output=True, text=True)


def stray_shas(root, base: str = 'origin/master') -> list[str]:
    """`path:line: sha why` for each full sha in a tracked file under
    .agents/skills that names no commit on `base`, or one line naming `base`
    when this clone lacks it."""
    if git(root, 'rev-parse', '-q', '--verify', f'{base}^{{commit}}').returncode:
        return [f'{base} names no commit in this clone (git fetch origin?)']
    files = git(root, 'ls-files', '-z', '--', '.agents/skills').stdout.split('\0')[:-1]
    found = []
    for name in files:
        try:
            text = (Path(root) / name).read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for sha in SHA.findall(line):
                if git(root, 'cat-file', '-e', f'{sha}^{{commit}}').returncode:
                    why = 'names no commit in this clone'
                elif git(root, 'merge-base', '--is-ancestor', sha, base).returncode:
                    why = f'is not on {base}'
                else:
                    continue
                found.append(f'{name}:{n}: {sha} {why}')
    return found


FIX = ('name the squash commit on master that holds the same code (`git log origin/master '
       '--grep "#N"`, and `git diff <branch sha> <squash sha>` shows no code change), in a '
       'follow-up after the PR merges (#499 retro)')


class SkillShasOnMaster(unittest.TestCase):
    def test_every_sha_a_skill_names_is_on_origin_master(self):
        stray = stray_shas(ROOT)
        if stray:
            self.fail('\n'.join(['', *stray, FIX]))


class StrayShas(unittest.TestCase):
    """stray_shas over a scratch repository: master holds `on`, a branch
    holds `off`."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')

        def run(*argv):
            subprocess.run(['git', '-C', str(self.root), *argv], check=True,
                           capture_output=True, env=env)
            return git(self.root, 'rev-parse', 'HEAD').stdout.strip()

        run('init', '-q', '-b', 'master')
        run('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty',
            '-m', 'on')
        run('update-ref', 'refs/remotes/origin/master', 'HEAD')
        self.on = git(self.root, 'rev-parse', 'HEAD').stdout.strip()
        run('checkout', '-q', '-b', 'side')
        self.off = run('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                       '--allow-empty', '-m', 'off')
        self.skill = self.root / '.agents' / 'skills' / 'x' / 'SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.run_git = run

    def write(self, text: str, track: bool = True):
        self.skill.write_text(text)
        if track:
            self.run_git('add', '.')

    def test_a_sha_on_master_passes(self):
        self.write(f'- **Last verified commit:** `{self.on}`\n')
        self.assertEqual(stray_shas(self.root), [])

    def test_a_branch_sha_is_refused_with_its_line(self):
        self.write(f'top\ngit diff --stat {self.off} -- a\n')
        self.assertEqual(stray_shas(self.root),
                         [f'.agents/skills/x/SKILL.md:2: {self.off} is not on origin/master'])

    def test_a_sha_this_clone_lacks_is_refused(self):
        gone = 'f' * 40
        self.write(f'sync commit: {gone}\n')
        self.assertEqual(stray_shas(self.root),
                         [f'.agents/skills/x/SKILL.md:1: {gone} names no commit in this clone'])

    def test_every_sha_of_a_line_is_read(self):
        self.write(f'{self.on} {self.off}\n')
        self.assertEqual(stray_shas(self.root),
                         [f'.agents/skills/x/SKILL.md:1: {self.off} is not on origin/master'])

    def test_a_longer_hex_run_is_no_sha(self):
        self.write(f'{self.off}0\n')
        self.assertEqual(stray_shas(self.root), [])

    def test_an_untracked_file_is_not_read(self):
        self.write(f'{self.off}\n', track=False)
        self.assertEqual(stray_shas(self.root), [])

    def test_a_hooks_git_variables_name_no_other_repository(self):
        """Git runs the pre-push hook with GIT_DIR set, and a caller may set
        GIT_INDEX_FILE: neither reaches the repository stray_shas reads
        (#431, #504 review)."""
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        subprocess.run(['git', 'init', '-q', other.name], check=True, capture_output=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})
        self.write(f'{self.off}\n')
        expected = [f'.agents/skills/x/SKILL.md:1: {self.off} is not on origin/master']
        for name, value in (('GIT_DIR', f'{other.name}/.git'),
                            ('GIT_INDEX_FILE', f'{other.name}/.git/index')):
            with self.subTest(name=name), unittest.mock.patch.dict(os.environ, {name: value}):
                self.assertEqual(stray_shas(self.root), expected)

    def test_a_clone_without_origin_master_fails(self):
        self.write(f'{self.on}\n')
        self.run_git('update-ref', '-d', 'refs/remotes/origin/master')
        self.assertEqual(stray_shas(self.root),
                         ['origin/master names no commit in this clone (git fetch origin?)'])


if __name__ == '__main__':
    unittest.main()
