"""Every check `server/docker/ci/checks.sh` defines runs in
`.github/workflows/image.yml`, and its header names it.

#58's first review round added the checks stop, reload, forwarded-proto,
engine, postgres-password, bad-env, upgrade and backup. They ran only locally
until 7391a51a, since the workflow's checks job named each check by hand and
listed none of them (#58 retro).
The checks job runs `checks.sh` with no arguments, which runs its default
list, and names each check left out of that list in a step of its own. Only a
step that runs counts: a `# run:` commented out, or a step with `if: false`,
gave 4 passed in #58's retro review (tests/workflow_steps.py).
"""

import re
from pathlib import Path

from tests.workflow_steps import steps

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
# A script line that runs checks.sh, after any VAR=value; a `#` line is no call.
CALL = re.compile(r"^(?:\S+=\S+ +)*server/docker/ci/checks\.sh((?: [a-z][a-z-]*)*) *$")


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
    """The checks the workflow's live steps run: a bare `checks.sh` runs the
    default list."""
    calls = [
        match.group(1).split()
        for step in steps(workflow)
        if step.runs
        for line in step.script.splitlines()
        if (match := CALL.match(line.strip()))
    ]
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
    stand_in = workflow(
        [
            f"server/docker/ci/checks.sh {name}"
            for name in arms(text)
            if name not in default
        ]
    )
    assert set(arms(text)) - run_by(stand_in, default) == set()
    stand_in = stand_in.replace("checks.sh backup", "checks.sh other")
    assert set(arms(text)) - run_by(stand_in, default) == {"backup"}


def workflow(calls: list[str], extra: str = "") -> str:
    """A stand-in checks job: a bare checks.sh, then a step per call."""
    out = "jobs:\n  checks:\n    steps:\n      - name: default\n"
    out += "        run: server/docker/ci/checks.sh\n"
    for n, call in enumerate(calls):
        out += f"      - name: step {n}\n{extra}        run: {call}\n"
    return out


def test_a_commented_out_step_runs_nothing():
    """#58's retro review commented out both backup steps as `# run:` and
    this file still passed."""
    text = CHECKS.read_text()
    default = default_list(text)
    stand_in = workflow(["server/docker/ci/checks.sh backup"])
    assert "backup" in run_by(stand_in, default)
    commented = stand_in.replace(
        "        run: server/docker/ci/checks.sh backup",
        "        # run: server/docker/ci/checks.sh backup",
    )
    assert "backup" not in run_by(commented, default)
    in_a_block = stand_in.replace(
        "        run: server/docker/ci/checks.sh backup",
        "        run: |\n          # server/docker/ci/checks.sh backup\n          true",
    )
    assert "backup" not in run_by(in_a_block, default)


def test_a_step_that_never_runs_runs_nothing():
    text = CHECKS.read_text()
    default = default_list(text)
    for never in ("false", "${{ false }}", "'${{ false }}'"):
        stand_in = workflow(
            ["server/docker/ci/checks.sh backup"], f"        if: {never}\n"
        )
        assert "backup" not in run_by(stand_in, default), never
    live = workflow(["server/docker/ci/checks.sh backup"], "        if: always()\n")
    assert "backup" in run_by(live, default)
    with_env = workflow(
        ["server/docker/ci/checks.sh backup"],
        "        env:\n          PROFILE: postgres\n",
    )
    assert "backup" in run_by(with_env, default)
