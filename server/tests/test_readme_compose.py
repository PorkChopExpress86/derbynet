"""The top-level README's Compose example is `server/docker/compose.yaml`
with no `build:` (D107).

The README holds a complete example a race crew copies as written, and
`server/docker/ci/checks.sh readme-compose` runs that text. compose.yaml is
the file the guide tells the crew to save, and it keeps a `build:` so a copy
of the repository can build the image when none is published. The two must
not drift: a fix to one that misses the other would ship an example no check
runs, or a file no check runs from the README. Both are compared from their
first line that is not a comment, since their headers say where each lives.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
COMPOSE = ROOT / "server" / "docker" / "compose.yaml"

YAML_FENCE = re.compile(r"^```yaml\n(.*?)^```$", re.M | re.S)


def readme_example(text: str) -> str:
    """The README's one ```yaml block."""
    blocks = YAML_FENCE.findall(text)
    assert len(blocks) == 1, f"README.md has {len(blocks)} ```yaml blocks, not one"
    return blocks[0]


def body(text: str) -> str:
    """The file from its first line that is not a comment or blank."""
    lines = text.splitlines(keepends=True)
    for n, line in enumerate(lines):
        if line.strip() and not line.lstrip().startswith("#"):
            return "".join(lines[n:])
    return ""


def without_build(text: str) -> str:
    """`text` with each `build:` key and the lines indented under it left out."""
    out, skip = [], None
    for line in text.splitlines(keepends=True):
        indent = len(line) - len(line.lstrip())
        if skip is not None and (not line.strip() or indent > skip):
            continue
        skip = None
        if line.strip() == "build:":
            skip = indent
            continue
        out.append(line)
    return "".join(out)


def test_the_readme_example_is_compose_yaml_without_build():
    example = readme_example(README.read_text())
    assert "build:" not in example, "the README's example builds; a crew has no checkout"
    assert body(example) == body(without_build(COMPOSE.read_text()))


STAND_IN = """\
# a header
name: derbynet

services:
  server:
    image: x
    build:
      context: ../..
      dockerfile: server/docker/Dockerfile
    restart: unless-stopped
"""


def test_without_build_leaves_out_the_block_alone():
    assert body(without_build(STAND_IN)) == (
        "name: derbynet\n\nservices:\n  server:\n    image: x\n    restart: unless-stopped\n"
    )


@pytest.mark.parametrize(
    "example, same",
    [
        (STAND_IN.replace("# a header\n", "# another header\n"), True),
        (STAND_IN.replace("restart: unless-stopped", "restart: always"), False),
        (STAND_IN.replace("name: derbynet", "name: other"), False),
    ],
    ids=["header-only", "changed-line", "changed-first-line"],
)
def test_a_difference_past_the_header_fails(example, same):
    assert (body(without_build(example)) == body(without_build(STAND_IN))) is same


@pytest.mark.parametrize(
    "readme, count",
    [
        ("text\n```yaml\na: 1\n```\n", 1),
        ("text\n```yaml\na: 1\n```\n```yaml\nb: 2\n```\n", 2),
        ("text\n```sh\na\n```\n", 0),
    ],
    ids=["one", "two", "none"],
)
def test_the_readme_holds_one_yaml_block(readme, count):
    assert len(YAML_FENCE.findall(readme)) == count
