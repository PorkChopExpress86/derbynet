"""The `run:` steps of a GitHub Actions workflow, read by indentation (the
project has no YAML parser), for tests that check what CI runs.

A commented-out `# run:` is no step, and a step with `if: false` never runs
(#58 retro: test_docker_checks.py counted both).
"""

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

RUN = re.compile(r"^(?P<indent> *)(?P<dash>- )?run:[ \t]*(?P<value>.*)$")
KEY = re.compile(r"^(?P<indent> *)(?:- )?(?P<key>[\w-]+):[ \t]*(?P<value>.*)$")
JOB = re.compile(r"^  (?P<job>[\w-]+):\s*$")
DEFAULT_SHELL = re.compile(
    r"^(?P<indent> *)defaults:\n(?P=indent)  run:\n"
    r"(?:(?P=indent)    [^\n]*\n)*?(?P=indent)    shell:[ \t]*(?P<shell>[^\n#]*)",
    re.M,
)
NEVER = {"false", "${{ false }}", "${{false}}"}


@dataclass(frozen=True)
class Step:
    line: int  # of its `run:` key, from 1
    job: str
    script: str
    shell: str  # its own `shell:`, else its job's or the workflow's default
    condition: str  # its `if:`, or ''

    @property
    def runs(self) -> bool:
        return self.condition.strip().strip("'\"") not in NEVER

    @property
    def pipefail(self) -> bool:
        """GitHub runs a step whose shell is `bash` as `bash --noprofile
        --norc -eo pipefail {0}`, and one with no shell given as `bash -e
        {0}`, without pipefail (image.yml's logs print `shell: /usr/bin/bash
        -e {0}`)."""
        return self.shell == "bash" or "pipefail" in self.shell


def _dedent(lines: list[str]) -> list[str]:
    widths = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    cut = min(widths, default=0)
    return [line[cut:] for line in lines]


def _shells(text: str) -> tuple[str, dict[str, str]]:
    """The workflow's default shell and each job's."""
    workflow, jobs = "", {}
    for match in DEFAULT_SHELL.finditer(text):
        line = text.count("\n", 0, match.start())
        shell = match.group("shell").strip()
        if match.group("indent") == "":
            workflow = shell
        elif match.group("indent") == "    ":
            jobs[_job_at(text.splitlines(), line)] = shell
    return workflow, jobs


def _job_at(lines: list[str], index: int) -> str:
    for i in range(index, -1, -1):
        if (match := JOB.match(lines[i])) and _under_jobs(lines, i):
            return match.group("job")
    return ""


def _under_jobs(lines: list[str], index: int) -> bool:
    for i in range(index - 1, -1, -1):
        if lines[i] and not lines[i].startswith(" ") and not lines[i].startswith("#"):
            return lines[i].rstrip() == "jobs:"
    return False


def steps(text: str) -> list[Step]:
    lines = text.splitlines()
    workflow_shell, job_shells = _shells(text)
    found = []
    for i, line in enumerate(lines):
        match = RUN.match(line)
        if not match:
            continue
        key_indent = len(match.group("indent")) + (2 if match.group("dash") else 0)
        value = match.group("value").strip()
        if not value:
            continue  # `defaults: run:`, a mapping, not a script
        if value.startswith(("|", ">")):
            body, j = [], i + 1
            while j < len(lines) and (
                not lines[j].strip()
                or len(lines[j]) - len(lines[j].lstrip(" ")) > key_indent
            ):
                body.append(lines[j])
                j += 1
            body = _dedent(body)
            script = (
                " ".join(b for b in body if b.strip())
                if value[0] == ">"
                else "\n".join(body)
            )
        else:
            script = value
        keys = _step_keys(lines, i, key_indent)
        job = _job_at(lines, i)
        shell = keys.get("shell") or job_shells.get(job) or workflow_shell
        found.append(Step(i + 1, job, script, shell, keys.get("if", "")))
    return found


def _step_keys(lines: list[str], index: int, indent: int) -> dict[str, str]:
    """The step's own keys: from its `- ` line to the next line at or left of
    the dash."""
    start = index
    while start >= 0:
        line = lines[start]
        if line.startswith(" " * (indent - 2) + "- "):
            break
        start -= 1
    keys = {}
    for j in range(max(start, 0), len(lines)):
        line = lines[j]
        width = len(line) - len(line.lstrip(" "))
        if j > start and line.strip() and width <= indent - 2:
            break
        match = KEY.match(line)
        if match and (width == indent or j == start):
            keys.setdefault(
                match.group("key"), match.group("value").split(" #")[0].strip()
            )
    return keys
