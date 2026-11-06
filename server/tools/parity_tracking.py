"""Keep one issue on the fork that lists the steps failing the nightly parity
gate (#218), so that a new failure shows on the day it appears instead of
behind the gate's expected red.

    uv run python -m tools.parity_tracking --run-url URL --sha SHA \\
        sqlite=failing-sqlite.json postgres=failing-postgres.json
    uv run python -m tools.parity_tracking --dry-run [--issue-body FILE] ...

Each `<database>=<path>` is one gate job's `parity_gate.py --failing` file.
The failing list is each failing step's label and the databases it fails
on. The open issue titled `TITLE` carries the list it last showed, in a
comment line at the top of its body. When tonight's list differs, the
script rewrites the body (each failing step, its difference count on each
database, and the WAITING entry it waits on) and comments with the steps
that started and stopped failing. When the list is the same it changes
nothing. With no such issue, it opens one.

Every `gh` call names `--repo PorkChopExpress86/derbynet`: the issue stays on
the owner's fork, never upstream (CLAUDE.md). `--dry-run` prints each call
that would write instead of making it, and reads the issue's body from
`--issue-body` (none when left out) instead of from GitHub.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = "PorkChopExpress86/derbynet"
TITLE = "Parity gate: failing steps"
MARKER = "<!-- parity-gate-failing: "


@dataclass(frozen=True)
class Leg:
    database: str
    steps: list[dict]
    short: list[str]


def read_legs(arguments: list[str]) -> list[Leg]:
    legs = []
    for argument in arguments:
        database, _, path = argument.partition("=")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        legs.append(Leg(database, data["steps"], data["short"]))
    return legs


def failing_list(legs: list[Leg]) -> dict[str, list[str]]:
    """Each failing step's label, and the databases it fails on, sorted. A
    leg cut short counts as a failing step of its own."""
    found: dict[str, set[str]] = {}
    for leg in legs:
        labels = [step["label"] for step in leg.steps]
        labels += ["(the leg was cut short)"] * bool(leg.short)
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
            if label == "(the leg was cut short)" and leg.short:
                cells.append("cut short")
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
        if self.issue_body is None:
            return "[]"
        return json.dumps([{"number": 0, "title": TITLE, "body": self.issue_body}])


def find_issue(gh: Gh) -> dict | None:
    found = json.loads(
        gh(
            "issue",
            "list",
            "--state",
            "open",
            "--search",
            f'in:title "{TITLE}"',
            "--limit",
            "50",
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
        gh("issue", "create", "--title", TITLE, "--body-file", str(body), writes=True)
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
    args = parser.parse_args(argv)
    body = args.issue_body.read_text(encoding="utf-8") if args.issue_body else None
    gh = Gh(args.dry_run, body)
    did = track(gh, read_legs(args.legs), args.run_url, args.sha, args.workdir)
    print(f"tracking issue {did}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
