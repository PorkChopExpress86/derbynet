"""The files a check reads by kind, by listing every file of the kind
rather than naming one, for the checks that import a package and so can't
be loaded by their path. plan/bin/affected-tests lists the same files
(KIND_READERS), so a new shell script maps to the pipe linters without
naming them (#431). Stdlib only, no import of the tests package, and syntax
python 3.12 parses: affected-tests loads this file by its path with the
host's python3.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEBANG = re.compile(r"^#!\s*\S*\b(?:ba)?sh\b|^#!\s*/usr/bin/env\s+(?:ba)?sh\b")
# Where a command a reader or a script runs is written.  Python tests hold
# stand-ins, test_docker_checks.py's own among them.
EXEC_SUFFIXES = (".md", ".sh", ".yml", ".yaml", ".html", ".txt", ".py")
# A name, since ruff's format for the server's python drops the parentheses
# of `except (A, B):`, which older pythons need.
UNREADABLE = (UnicodeDecodeError, OSError)


def tracked() -> list[str]:
    return [
        name
        for name in subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
        if name
    ]


def shell_script(name: str) -> bool:
    """A tracked file outside website/ that ends in `.sh` or opens on a
    bash or sh shebang: what the pipefail and subshell linters read."""
    path = ROOT / name
    if name.startswith("website/") or not path.is_file():
        return False
    if name.endswith(".sh"):
        return True
    try:
        with path.open(encoding="utf-8") as f:
            first = f.readline()
    except UNREADABLE:
        return False
    return bool(SHEBANG.match(first))


def command_file(name: str) -> bool:
    """A file outside website/ where a `docker exec ... manage.py` command
    may be written, other than a Python test."""
    return (
        not name.startswith("website/")
        and name.endswith(EXEC_SUFFIXES)
        and not (name.endswith(".py") and "/tests/" in name)
        and (ROOT / name).is_file()
    )


def shell_files() -> list[Path]:
    return [ROOT / name for name in tracked() if shell_script(name)]


def exec_files() -> list[str]:
    return [name for name in tracked() if command_file(name)]


def suite_scripts() -> list[Path]:
    """The `.sh` scripts at the top of testing/, and the helpers with no
    suffix such as `queryj`."""
    return sorted(
        path
        for path in (ROOT / "testing").iterdir()
        if path.is_file()
        and (
            path.suffix == ".sh"
            or (not path.suffix and path.read_bytes().startswith(b"#!"))
        )
    )


def top_of_testing() -> list[Path]:
    """The files at the top of testing/, but for the `.curl` files a run of
    the suite leaves there."""
    return sorted(
        path
        for path in (ROOT / "testing").iterdir()
        if path.is_file() and not path.name.endswith(".curl")
    )


def headshots() -> list[Path]:
    return sorted((ROOT / "testing" / "data" / "headshots").glob("*.jpg"))
