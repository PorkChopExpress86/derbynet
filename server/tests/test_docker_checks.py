"""Every check `server/docker/ci/checks.sh` defines runs in
`.github/workflows/image.yml`, and its header names it.

#58's first review round added the checks stop, reload, forwarded-proto,
engine, postgres-password, bad-env, upgrade and backup. They ran only locally
until 7391a51a, since the workflow's checks job named each check by hand and
listed none of them (#58 retro).
The checks job runs `checks.sh` with no arguments, which runs its default
list, and names each check left out of that list in a step of its own.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ROOT / "server" / "docker" / "ci" / "checks.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

ARM = re.compile(r"^ +([a-z][a-z-]*)\) (check_\w+) ;;$", re.M)
FUNCTION = re.compile(r"^(check_\w+)\(\) \{", re.M)
DEFAULT = re.compile(r"^\[ \$# -gt 0 \] \|\| set -- ((?:[^\n]*\\\n)*[^\n]*)$", re.M)
# The header's list under `# Checks:`: a name, then what it checks, or the
# name alone on its line when it is too long for the column.
HEADER = re.compile(r"^# Checks:\n((?:#[^\n]*\n)*?)#\n", re.M)
HEADER_NAME = re.compile(r"^#   ([a-z][a-z-]*)(?: |$)", re.M)
CALL = re.compile(r"server/docker/ci/checks\.sh((?: [a-z][a-z-]*)*) *$", re.M)


def arms(text: str) -> dict[str, str]:
    return dict(ARM.findall(text))


def default_list(text: str) -> list[str]:
    match = DEFAULT.search(text)
    assert match, "checks.sh sets no default list with `[ $# -gt 0 ] || set -- ...`"
    return match.group(1).replace("\\\n", " ").split()


def header_names(text: str) -> list[str]:
    match = HEADER.search(text)
    assert match, "checks.sh's header has no `# Checks:` list ending in a bare `#`"
    return HEADER_NAME.findall(match.group(1))


def run_by(workflow: str, default: list[str]) -> set[str]:
    """The checks the workflow runs: a bare `checks.sh` runs the default list."""
    calls = [c.split() for c in CALL.findall(workflow)]
    assert calls, "image.yml runs no server/docker/ci/checks.sh"
    return {name for call in calls for name in (call or default)}


def test_each_check_function_has_its_name():
    text = CHECKS.read_text()
    named = arms(text)
    assert sorted(named.values()) == sorted(FUNCTION.findall(text))
    assert {f"check_{n.replace('-', '_')}" for n in named} == set(named.values())


def test_the_header_names_each_check():
    text = CHECKS.read_text()
    assert sorted(header_names(text)) == sorted(arms(text))


def test_image_yml_runs_each_check():
    text = CHECKS.read_text()
    default = default_list(text)
    assert set(default) <= set(arms(text)), "the default list names no such check"
    ran = run_by(WORKFLOW.read_text(), default)
    unknown = ran - set(arms(text))
    assert not unknown, f"image.yml runs no such check: {unknown}"
    missing = sorted(set(arms(text)) - ran)
    assert missing == [], (
        f"image.yml never runs {missing}: add each to checks.sh's default list, "
        "or give it a step of its own in image.yml's checks job"
    )


def test_a_check_left_out_of_the_workflow_fails():
    """The guard against a workflow that drops a check: the stand-in names
    every check but backup."""
    text = CHECKS.read_text()
    default = default_list(text)
    stand_in = (
        "\n".join(
            f"        run: server/docker/ci/checks.sh {name}"
            for name in arms(text)
            if name not in default and name != "backup"
        )
        + "\n        run: server/docker/ci/checks.sh\n"
    )
    assert set(arms(text)) - run_by(stand_in, default) == {"backup"}
