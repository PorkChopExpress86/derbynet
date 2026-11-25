"""`testing/docker/php-scratch.sh` reads its images from
DERBYNET_PHP_IMAGES when set, and names its container and volume after
DERBYNET_TEST_PREFIX either way: #356 and #360 each tagged and untagged
derbynet-pollres-* to use a prefix of their own (#360 retro). A stand-in
`docker` records each call and answers as the images exist."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "testing" / "docker" / "php-scratch.sh"

# Records each call's arguments on a line of its own, and says an image
# exists only when its name starts with STUB_IMAGES.
DOCKER = """#! /bin/sh
echo "$*" >> "$(dirname "$0")/calls"
case "$1 $2" in
  "image inspect") case "$3" in "$STUB_IMAGES"-*) exit 0;; *) exit 1;; esac;;
esac
exit 0
"""


def scratch(tmp_path, images, env, script=SCRIPT):
    """php-scratch.sh on a script name with the stand-in docker first on
    PATH: (its exit code, its stderr, the docker calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(DOCKER)
    (bin_dir / "docker").chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "test-points-racing.sh"],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "STUB_IMAGES": images,
            **env,
        },
    )
    calls = (bin_dir / "calls").read_text().splitlines()
    return result.returncode, result.stderr, calls


@pytest.mark.parametrize(
    "env, images",
    [
        ({"DERBYNET_TEST_PREFIX": "r1", "DERBYNET_PHP_IMAGES": "pollres"}, "pollres"),
        ({"DERBYNET_TEST_PREFIX": "r1"}, "r1"),
    ],
    ids=["images-named", "images-from-prefix"],
)
def test_the_images_come_from_php_images_and_the_names_from_the_prefix(
    tmp_path, env, images
):
    code, err, calls = scratch(tmp_path, images, env)
    assert code == 0, err
    assert f"image inspect {images}-harness" in calls
    assert f"image inspect {images}-server" in calls
    run = [c for c in calls if c.startswith("run -d ")]
    assert len(run) == 1
    assert run[0].startswith("run -d --name r1-scratch-")
    assert run[0].endswith(f"/var/lib/derbynet {images}-server")
    assert any(c.startswith("volume create r1-scratch-") for c in calls)
    harness = [c for c in calls if c.startswith("run --rm ")]
    assert harness and all(f" {images}-harness " in c for c in harness)


def clone_with_worktree(tmp_path):
    """A regular clone holding php-scratch.sh, and a linked worktree of it
    beside it."""
    clone = tmp_path / "clone"
    (clone / "testing" / "docker").mkdir(parents=True)
    (clone / "testing" / "docker" / "php-scratch.sh").write_text(SCRIPT.read_text())

    def git(*argv):
        subprocess.run(
            ["git", "-C", str(clone), *argv],
            check=True,
            capture_output=True,
            env={**os.environ, "HOME": str(tmp_path)},
        )

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    git("worktree", "add", "-q", str(tmp_path / "wt"))
    return clone, tmp_path / "wt"


@pytest.mark.parametrize("where", ["clone", "worktree"])
def test_a_missing_image_names_the_build_in_the_regular_clone(tmp_path, where):
    """From the clone or from its worktree, the command cds to the clone,
    since run-suite.sh can't build from a worktree, and builds the images
    under the name this run reads them by (#499 retro)."""
    clone, worktree = clone_with_worktree(tmp_path)
    root = clone if where == "clone" else worktree
    code, err, _ = scratch(
        tmp_path,
        "other",
        {"DERBYNET_TEST_PREFIX": "r1", "DERBYNET_PHP_IMAGES": "pollres"},
        script=root / "testing" / "docker" / "php-scratch.sh",
    )
    assert code == 1
    assert err == (
        "No image pollres-harness.  Build the images from the regular clone with:\n"
        f"  cd {clone} && DERBYNET_TEST_PREFIX=pollres testing/docker/run-suite.sh"
        " harness assets server\n"
    )
