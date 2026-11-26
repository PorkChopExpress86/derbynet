"""A workflow's own `env:` and a job's `env:` use only the contexts GitHub
makes available there.

GitHub's context availability table allows `github`, `inputs`, `secrets`
and `vars` in a workflow's `env`, and adds `matrix`, `needs` and `strategy`
in a job's `env`; `runner`, `env`, `job` and `steps` are not available
there. Such a workflow fails before any job starts, and its check never
appears on the pull request: #59's Pi image workflow (D139 deleted it) set
`LOGS: ${{ runner.temp }}/logs` in its checks job's env, and its run failed
as a workflow file issue (#59 review).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

WORKFLOW_ENV = {"github", "inputs", "secrets", "vars"}
JOB_ENV = WORKFLOW_ENV | {"matrix", "needs", "strategy"}
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
QUOTED = re.compile(r"'(?:[^']|'')*'")
CONTEXT = re.compile(r"(?<![\w.])([A-Za-z_][\w-]*)\s*[.\[]")


def env_values(text):
    """(line number, allowed contexts, value) for each value under the
    workflow's `env:` (column 0) and under each job's `env:` (column 4,
    under `jobs:`).  It reads a value on its key's line, not a block
    scalar's lines below it; no workflow puts a block scalar in an env."""
    found = []
    allowed = None
    child = None
    in_jobs = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_jobs = stripped == "jobs:"
        if child is not None and indent < child:
            allowed = child = None
        if stripped == "env:" and (indent == 0 or (in_jobs and indent == 4)):
            allowed = WORKFLOW_ENV if indent == 0 else JOB_ENV
            child = indent + 2
            continue
        if allowed is not None and indent == child and ":" in stripped:
            found.append((number, allowed, stripped.split(":", 1)[1]))
    return found


def unavailable(allowed, value):
    """The contexts `value`'s expressions use that `allowed` leaves out."""
    names = set()
    for expression in EXPRESSION.findall(value):
        names |= set(CONTEXT.findall(QUOTED.sub("''", expression)))
    return sorted(names - allowed)


def test_the_rule_finds_the_runner_context_in_a_job_env():
    text = (
        "on: push\njobs:\n  checks:\n    runs-on: x\n    env:\n"
        "      A: ${{ matrix.check }}\n      LOGS: ${{ runner.temp }}/logs\n"
        "    steps:\n      - run: echo ${{ runner.temp }}\n"
    )
    bad = [(n, unavailable(a, v)) for n, a, v in env_values(text) if unavailable(a, v)]
    assert bad == [(7, ["runner"])]


def test_the_rule_finds_matrix_in_the_workflow_env():
    text = "on: push\nenv:\n  A: ${{ matrix.x == 'a.b' && github.ref }}\njobs:\n"
    bad = [(n, unavailable(a, v)) for n, a, v in env_values(text) if unavailable(a, v)]
    assert bad == [(3, ["matrix"])]


def findings(text):
    return [(n, unavailable(a, v)) for n, a, v in env_values(text) if unavailable(a, v)]


def test_each_context_the_table_allows_passes():
    text = (
        "on: push\n"
        "env:\n"
        "  A: ${{ github.ref }} ${{ inputs.a }} ${{ secrets.b }} ${{ vars.c }}\n"
        "jobs:\n  checks:\n    env:\n"
        "      B: ${{ github.ref }} ${{ inputs.a }} ${{ secrets.b }} ${{ vars.c }}\n"
        "      C: ${{ matrix.d }} ${{ needs.build.result }} ${{ strategy.job-index }}\n"
    )
    assert env_values(text) and findings(text) == []


def test_a_blank_line_or_comment_under_jobs_keeps_the_job_env_read():
    text = (
        "on: push\njobs:\n\n# the checks\n  checks:\n    env:\n"
        "      LOGS: ${{ runner.temp }}\n"
    )
    assert findings(text) == [(7, ["runner"])]


def test_an_env_at_column_4_outside_jobs_is_no_job_env():
    text = "on:\n  workflow_call:\n    env:\n      A: ${{ runner.temp }}\njobs:\n"
    assert env_values(text) == []


@pytest.mark.parametrize(
    "workflow", sorted(WORKFLOWS.glob("*.y*ml")), ids=lambda p: p.name
)
def test_each_env_uses_only_its_available_contexts(workflow):
    bad = [
        (number, value.strip(), unavailable(allowed, value))
        for number, allowed, value in env_values(workflow.read_text())
        if unavailable(allowed, value)
    ]
    assert bad == []
