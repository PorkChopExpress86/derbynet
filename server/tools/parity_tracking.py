"""Keep one issue on the fork that lists the steps failing the nightly parity
gate (#218), so that a new failure shows on the day it appears instead of
behind the gate's expected red.

    uv run python -m tools.parity_tracking --run-url URL --sha SHA \\
        sqlite=failing-sqlite.json postgres=failing-postgres.json
    uv run python -m tools.parity_tracking --dry-run [--issue-body FILE] ...

Each `<database>=<path>` is one gate job's `parity_gate.py --failing` file.
A gate job that died before writing it counts as one failing entry, `(the
leg did not report)`, so the issue still changes. `--jobs` is the run's `gh
run view --json jobs`: a leg job (`leg (php)`, `leg (django, <database>)`)
that did not succeed counts as one failing entry per failed step, `(the
leg's job failed: <step>)`, on its database (`leg (php)` on every one), so a
second step that starts failing changes the list too. A leg uploads its
steps before it replays the golden fixtures and races derby-timer.jar, so
the gate passes when only those fail: from 2026-11-11 the nightly was red on
a missing Chromium while this issue said no step failed (#206 retro). A
`--jobs` file it cannot read, or one that lacks `leg (php)` or a database's
`leg (django, <database>)`, counts as `(the leg jobs could not be read)`
there, so a renamed or missing job cannot clear the list. The failing list
is each failing step's label and the databases it fails on. The open issue
labeled `LABEL` and titled `TITLE` carries the list it last showed, in a
comment line at the top of its body. It is found by its label, not by a
search, whose index can lag behind a new issue and open a second one. When
tonight's list differs, the script rewrites the body (each failing step, its
difference count on each database, and the WAITING entry it waits on) and
comments with the steps that started and stopped failing. When the list is
the same it changes nothing. With no such issue, it opens one.

Every `gh` call names `--repo PorkChopExpress86/derbynet`: the issue stays on
the owner's fork, never upstream (CLAUDE.md). `--dry-run` prints each call
that would write instead of making it, and reads the issue's body from
`--issue-body` (none when left out) instead of from GitHub.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

REPO = "PorkChopExpress86/derbynet"
TITLE = "Parity gate: failing steps"
LABEL = "parity-gate"
LABEL_COLOR = "d93f0b"
NO_REPORT = "(the leg did not report)"
CUT_SHORT = "(the leg was cut short)"
JOB_FAILED = "(the leg's job failed: {})"
JOBS_UNREAD = "(the leg jobs could not be read)"
LEG_JOB = re.compile(r"leg \((?:php|django, (\w+))\)")
MARKER = "<!-- parity-gate-failing: "


@dataclass(frozen=True)
class Leg:
    database: str
    steps: list[dict]
    short: list[str]
    reported: bool = True
    # Each failing entry of the leg's job, JOB_FAILED or JOBS_UNREAD, with
    # the job that failed or why the jobs file could not be read.
    job: tuple[tuple[str, str], ...] = ()


def read_legs(arguments: list[str]) -> list[Leg]:
    legs = []
    for argument in arguments:
        database, _, path = argument.partition("=")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            legs.append(Leg(database, data["steps"], data["short"]))
        except OSError, ValueError, KeyError, TypeError:
            reason = f"the gate job wrote no readable {Path(path).name}"
            legs.append(Leg(database, [], [reason], reported=False))
    return legs


def read_jobs(legs: list[Leg], path: Path) -> list[Leg]:
    """`legs`, each with its leg job's failures from `path`, the run's
    `gh run view --json jobs`."""
    try:
        jobs = json.loads(path.read_text(encoding="utf-8"))["jobs"]
        found: dict[str, list[tuple[str, str]]] = {leg.database: [] for leg in legs}
        seen = set()
        for job in jobs:
            leg_job = LEG_JOB.fullmatch(job["name"])
            if leg_job:
                seen.add(job["name"])
            if not leg_job or job["conclusion"] in ("success", "skipped"):
                continue
            steps = [
                step["name"]
                for step in job.get("steps") or []
                if step.get("conclusion") == "failure"
            ]
            failures = [
                (JOB_FAILED.format(step), job["name"])
                for step in steps or [job["conclusion"] or job.get("status")]
            ]
            for database in [leg_job[1]] if leg_job[1] else list(found):
                found.setdefault(database, []).extend(failures)
    except (OSError, ValueError, KeyError, TypeError) as error:
        why = ((JOBS_UNREAD, f"{path.name}: {error!r}"),)
        return [replace(leg, job=why) for leg in legs]
    for database, entries in found.items():
        for name in ("leg (php)", f"leg (django, {database})"):
            if name not in seen:
                entries.append((JOBS_UNREAD, f"{path.name} has no {name}"))
    return [replace(leg, job=tuple(found.get(leg.database, ()))) for leg in legs]


def _extra(leg: Leg) -> list[str]:
    extra = [NO_REPORT] if not leg.reported else [CUT_SHORT] if leg.short else []
    return extra + [label for label, _ in leg.job if label not in extra]


def failing_list(legs: list[Leg]) -> dict[str, list[str]]:
    """Each failing step's label, and the databases it fails on, sorted. A
    leg cut short counts as a failing step of its own."""
    found: dict[str, set[str]] = {}
    for leg in legs:
        labels = [step["label"] for step in leg.steps] + _extra(leg)
        for label in labels:
            found.setdefault(label, set()).add(leg.database)
    return {label: sorted(databases) for label, databases in sorted(found.items())}


def shown_list(body: str | None) -> dict[str, list[str]] | None:
    """The failing list an issue body carries, or None."""
    if not body:
        return None
    for line in body.splitlines():
        if line.startswith(MARKER) and line.endswith(" -->"):
            return json.loads(line[len(MARKER) : -len(" -->")])
    return None


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(legs: list[Leg], run_url: str, sha: str) -> str:
    listed = failing_list(legs)
    databases = [leg.database for leg in legs]
    out = [
        MARKER + json.dumps(listed, sort_keys=True) + " -->",
        "The steps that fail the nightly parity gate (`.github/workflows/parity.yml`,"
        " `server/tools/parity_gate.py`). `server/tools/parity_tracking.py` rewrites"
        " this body when the list of failing steps changes, and comments with what"
        " changed; it leaves the issue alone while the list stays the same, so the"
        " counts are those of the run that last changed it.",
        "",
        f"Run: {run_url} at `{sha[:12]}`.",
        "",
    ]
    if not listed:
        return "\n".join([*out, "No step fails the gate."])
    out += [
        "| step | " + " | ".join(databases) + " | waits on | problems |",
        "|---|" + "---|" * len(databases) + "---|---|",
    ]
    for label in listed:
        cells, waiting, problems = [], None, []
        for leg in legs:
            step = next((s for s in leg.steps if s["label"] == label), None)
            job = [why for entry, why in leg.job if entry == label]
            if job:
                cells.append("not read" if label == JOBS_UNREAD else "job failed")
                problems += [p for p in job if p not in problems]
            elif label in _extra(leg):
                cells.append("did not report" if label == NO_REPORT else "cut short")
                problems += [p for p in leg.short if p not in problems]
            elif step is None:
                cells.append("passes")
            else:
                cells.append(f"{step['differences']} differences")
                waiting = waiting or step["waiting"]
                problems += [p for p in step["problems"] if p not in problems]
        out.append(
            f"| `{_cell(label)}` | "
            + " | ".join(cells)
            + f" | {_cell(waiting or 'no WAITING entry')}"
            + f" | {_cell('; '.join(problems))} |"
        )
    return "\n".join(out)


def change_comment(old: dict | None, new: dict, run_url: str) -> str:
    old = old or {}
    lines = [f"The list of failing steps changed in {run_url}."]
    for label in new:
        if label not in old:
            lines.append(f"- Now fails: `{label}` ({', '.join(new[label])})")
        elif old[label] != new[label]:
            lines.append(
                f"- `{label}` now fails on {', '.join(new[label])},"
                f" was {', '.join(old[label])}"
            )
    for label in old:
        if label not in new:
            lines.append(f"- No longer fails: `{label}`")
    return "\n".join(lines)


class Gh:
    """Runs `gh`, always against the fork."""

    def __init__(self, dry_run: bool = False, issue_body: str | None = None):
        self.dry_run = dry_run
        self.issue_body = issue_body
        self.calls: list[list[str]] = []

    def __call__(self, *args: str, writes: bool = False) -> str:
        command = ["gh", *args, "--repo", REPO]
        self.calls.append(command)
        if self.dry_run:
            if writes:
                print("would run:", " ".join(command))
                return ""
            return self._canned(args)
        return subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout

    def _canned(self, args) -> str:
        """What `gh issue list` answers in a dry run: the issue whose body is
        `--issue-body`, or none."""
        if self.issue_body is None or "--label" not in args:
            return "[]"
        return json.dumps([{"number": 0, "title": TITLE, "body": self.issue_body}])


def find_issue(gh: Gh) -> dict | None:
    """The open issue with `LABEL` and `TITLE`. A label filter lists the
    repository's issues directly, with no search index to lag."""
    found = json.loads(
        gh(
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            LABEL,
            "--limit",
            "100",
            "--json",
            "number,title,body",
        )
    )
    return next((issue for issue in found if issue["title"] == TITLE), None)


def track(gh: Gh, legs: list[Leg], run_url: str, sha: str, workdir: Path) -> str:
    """Open or update the tracking issue. Returns what it did: `opened`,
    `updated` or `unchanged`."""
    new = failing_list(legs)
    issue = find_issue(gh)
    old = shown_list(issue["body"]) if issue else None
    if issue is not None and old == new:
        return "unchanged"
    body = workdir / "parity-tracking-body.md"
    body.write_text(render(legs, run_url, sha), encoding="utf-8")
    if issue is None:
        # --force makes the label if the fork lacks it, and changes nothing
        # if it has it.
        gh(
            "label",
            "create",
            LABEL,
            "--color",
            LABEL_COLOR,
            "--description",
            "The nightly parity gate's failing steps (#218)",
            "--force",
            writes=True,
        )
        gh(
            "issue",
            "create",
            "--title",
            TITLE,
            "--label",
            LABEL,
            "--body-file",
            str(body),
            writes=True,
        )
        return "opened"
    number = str(issue["number"])
    gh("issue", "edit", number, "--body-file", str(body), writes=True)
    comment = workdir / "parity-tracking-comment.md"
    comment.write_text(change_comment(old, new, run_url), encoding="utf-8")
    gh("issue", "comment", number, "--body-file", str(comment), writes=True)
    return "updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("legs", nargs="+", help="<database>=<failing.json>")
    parser.add_argument("--run-url", default="(a local run)")
    parser.add_argument("--sha", default="unknown")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--issue-body", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--jobs", type=Path, help="the run's gh run view --json jobs")
    args = parser.parse_args(argv)
    body = args.issue_body.read_text(encoding="utf-8") if args.issue_body else None
    gh = Gh(args.dry_run, body)
    legs = read_legs(args.legs)
    if args.jobs:
        legs = read_jobs(legs, args.jobs)
    # The list in the log too: a dry run off master shows what it read.
    print("failing:", json.dumps(failing_list(legs), sort_keys=True))
    did = track(gh, legs, args.run_url, args.sha, args.workdir)
    print(f"tracking issue {did}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
