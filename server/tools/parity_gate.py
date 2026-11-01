"""The parity gate's verdict (#37): compare a PHP leg and a Django leg of the
shell suite, step by step.

    uv run python -m tools.parity_gate PHP_STEPS DJANGO_STEPS
    uv run python -m tools.parity_gate --summary "$GITHUB_STEP_SUMMARY" A B

Each leg is `testing/exercise-one-database.sh` run with `PARITY_DIR` set
(`testing/steps.sh`, `testing/docker/run-suite.sh parity`): a `steps.tsv`
with one line per step, and each step's slice of `output.curl`. The legs run
the same steps in the same order, so a step is paired with the step of the
same label.

The gate fails when:

- a PHP step fails. PHP is the reference, and `php-suite` passes it whole.
- a Django step fails, or its requests part from PHP's, unless the step is
  in `WAITING`, the steps that wait on another ticket: a waiting step may
  fail, and so stop where PHP's run goes on.
- a reply differs from PHP's, in any step, waiting or not, unless the
  registry allows it.
- a step in `WAITING` passes on Django. Its ticket landed, so remove the
  entry.
- a step ran in one leg and not in the other, other than a skip the leg
  records with its reason (`skip_step`).

A step in `AFTER_SKIP` shows the state of a step one leg skipped, so its
replies may differ, until it matches. A difference in a reply is allowed
when the registry's `PARITY_PATHS` covers its endpoint, path and values
(`legacy_api.registry.allowed_by`). A step in
`STEP_DIVERGENCES` asks the two servers different questions on purpose, so
its transcripts are compared up to where they part. Every allowed
difference, waiting step, skip and retry is printed, so nothing is hidden.
"""

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools.transcript_diff import MISSING, Difference, Report, diff

_INDEX = re.compile(r"\[\d+\]")

# The Django leg's steps that wait on another ticket, by label, with the
# ticket that makes them pass. Remove an entry when its ticket lands; the gate
# fails while a listed step passes, so the list can't go stale. A waiting step
# may fail and stop early, but a reply it gets is still compared.
_PAGES = (
    "the legacy pages: #40, #41, #42, #43, #44, #45, #46, #47, #48, #49, #50,"
    " #52, #53, #54"
)
_EACH_ROLE = f"{_PAGES} (test-visit-each-page.sh opens every page)"
WAITING: dict[str, str] = {
    "test-each-role.sh": _EACH_ROLE,
    "test-permissions.sh": f"{_PAGES} (fixtures/role-pages.json)",
    "test-basic-racing.sh": "#49 (history.php)",
    "puppeteer_test all-pages-test.js": _PAGES,
    "puppeteer_test coordinator-test.js": "#41 (coordinator.php)",
    "puppeteer_test ondeck-columns-test.js": "#52 (ondeck.php)",
    "test-each-role.sh (2)": _EACH_ROLE,
    "test-each-role.sh (3)": _EACH_ROLE,
    "puppeteer_test checkin-empty-test.js": "#42 (checkin.php)",
    "test-each-role.sh (4)": _EACH_ROLE,
    "test-each-role.sh (5)": _EACH_ROLE,
    "test-each-role.sh (6)": _EACH_ROLE,
}

# The steps whose requests differ between the legs on purpose, because the
# script asks Django other questions when DERBYNET_BACKEND=django. Their
# transcripts are compared up to where the requests part.
STEP_DIVERGENCES: dict[str, str] = {
    "test-reschedule.sh": (
        "D34, D39: with DERBYNET_BACKEND=django the script checks Django's"
        " charts and refusals, and races other heats (testing/README.md)"
    ),
}

# The steps after a skipped step whose replies show the state that step would
# have left, up to the next reset. Their replies may differ from PHP's; their
# requests and outcomes are still judged.
AFTER_SKIP: dict[str, str] = {
    # test-balloting.sh resets the database and builds its own; without it,
    # the snapshot holds the roster test-unused-lanes.sh left.
    "snapshot_get": "#54 (the state test-balloting.sh leaves)",
    # snapshot.put loads the snapshot snapshot_get took.
    "snapshot_put": "#54 (the snapshot taken without test-balloting.sh)",
}

# The differences, and the allowed ones, listed for each step; the counts
# cover the rest.
LISTED = 20


@dataclass(frozen=True)
class Step:
    number: int
    label: str
    outcome: str  # pass, fail or skip
    attempts: int
    transcript: Path | None
    reason: str

    @property
    def retried(self) -> bool:
        return self.attempts > 1


def read_leg(directory: Path) -> list[Step]:
    """The steps a leg recorded in `steps.tsv`, in order."""
    steps = []
    tsv = directory / "steps.tsv"
    if not tsv.exists():
        return steps
    for line in tsv.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        number, label, outcome, attempts, file, reason = (line.split("\t") + [""])[:6]
        steps.append(
            Step(
                int(number),
                label,
                outcome,
                int(attempts),
                directory / file if file else None,
                reason,
            )
        )
    return steps


def _allow(difference: Difference) -> str | None:
    from legacy_api.registry import allowed_by

    return allowed_by(difference.endpoint, difference.path, difference.a, difference.b)


@dataclass
class StepResult:
    label: str
    php: Step | None
    django: Step | None
    report: Report | None = None
    problems: list[str] = field(default_factory=list)
    waiting: str | None = None
    diverges_by: str | None = None
    after_skip: str | None = None

    @property
    def matches(self) -> bool:
        return self.report is not None and self.report.ok

    @property
    def failed(self) -> bool:
        return bool(self.problems)


def _read(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def judge(
    php: list[Step],
    django: list[Step],
    now: datetime | None = None,
    waiting: dict[str, str] | None = None,
    step_divergences: dict[str, str] | None = None,
    allow=_allow,
    after_skip: dict[str, str] | None = None,
) -> list[StepResult]:
    waiting = WAITING if waiting is None else waiting
    after_skip = AFTER_SKIP if after_skip is None else after_skip
    step_divergences = (
        STEP_DIVERGENCES if step_divergences is None else step_divergences
    )
    by_label = {s.label: s for s in django}
    results = []
    for step in php:
        other = by_label.pop(step.label, None)
        result = StepResult(
            step.label,
            step,
            other,
            waiting=waiting.get(step.label),
            after_skip=after_skip.get(step.label),
        )
        results.append(result)
        if step.outcome == "fail":
            result.problems.append("fails on PHP")
        if other is None:
            result.problems.append("the Django leg never ran it")
            continue
        if step.outcome == "skip" or other.outcome == "skip":
            continue
        report = diff(_read(step.transcript), _read(other.transcript), allow, now)
        result.report = report
        if report.diverged and step.label in step_divergences:
            result.diverges_by = step_divergences[step.label]
        # What a waiting step may do: fail on Django, and so stop sending
        # requests where PHP's run goes on.
        excused = []
        if other.outcome == "fail":
            excused.append("fails on Django")
        stopped = report.diverged is not None and report.diverged.b is MISSING
        parted = report.diverged is not None and result.diverges_by is None
        if parted and stopped:
            excused.append("stops before PHP's run ends")
        elif parted:
            result.problems.append("the requests part from PHP's")
        # A difference in a reply it did get fails the gate, waiting or not.
        if report.differences and result.after_skip is None:
            result.problems.append(f"{len(report.differences)} differences from PHP")
        elif result.after_skip is not None and not report.differences:
            result.problems.append(
                f"matches PHP although it follows a skip ({result.after_skip}):"
                " remove it from AFTER_SKIP in server/tools/parity_gate.py"
            )
        if result.waiting is None:
            result.problems += excused
        elif not excused:
            result.problems.append(
                f"waits on {result.waiting} but passes on Django:"
                " remove it from WAITING in server/tools/parity_gate.py"
            )
    for label, other in by_label.items():
        results.append(
            StepResult(label, None, other, problems=["the PHP leg never ran it"])
        )
    for label in waiting:
        if not any(r.label == label for r in results):
            results.append(
                StepResult(
                    label,
                    None,
                    None,
                    problems=["is in WAITING but no leg ran it: remove the entry"],
                )
            )
    return results


def _outcome(step: Step | None) -> str:
    if step is None:
        return "not run"
    text = step.outcome
    if step.retried:
        text += " after a retry"
    if step.reason:
        text += f" ({step.reason})"
    return text


def render(results: list[StepResult], labels: tuple[str, str]) -> str:
    php_label, django_label = labels
    failed = [r for r in results if r.failed]
    out = [
        f"## Parity gate: {php_label} against {django_label}",
        "",
        f"{len(results)} steps, {len(failed)} failing the gate.",
        "",
        f"| # | step | {php_label} | {django_label} | transcript | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for n, r in enumerate(results, 1):
        if r.report is None:
            transcript = "not compared"
        elif r.report.ok:
            transcript = "matches"
            if r.report.allowed:
                transcript += f", {len(r.report.allowed)} allowed"
        else:
            transcript = f"{len(r.report.differences)} differences"
            if r.report.diverged:
                transcript += ", requests part"
        if r.diverges_by:
            transcript += f" (parts on purpose: {r.diverges_by})"
        if r.failed:
            verdict = "FAIL: " + "; ".join(r.problems)
        elif r.waiting:
            verdict = f"waiting on {r.waiting}"
        elif r.after_skip:
            verdict = f"follows a skip: {r.after_skip}"
        else:
            verdict = "ok"
        out.append(
            f"| {n} | `{r.label}` | {_outcome(r.php)} | {_outcome(r.django)}"
            f" | {transcript} | {verdict} |"
        )
    retried = [
        f"{leg} `{step.label}`"
        for r in results
        for leg, step in ((php_label, r.php), (django_label, r.django))
        if step is not None and step.retried
    ]
    out += ["", "Retried once: " + (", ".join(retried) if retried else "none") + "."]
    out += _by_path(results)
    for r in results:
        if r.report is None or (r.report.ok and not r.report.allowed):
            continue
        out += [
            "",
            f"### `{r.label}`",
            "",
            "```",
            r.report.render(labels, LISTED),
            "```",
        ]
    return "\n".join(out)


def _by_path(results: list[StepResult]) -> list[str]:
    """The differences that fail the gate, by endpoint and path with any list
    index as `[*]`: what to fix, or to record as a divergence."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    steps: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in results:
        if r.report is None or r.after_skip:
            continue
        for difference in r.report.differences:
            key = (difference.endpoint, _INDEX.sub("[*]", difference.path))
            counts[key] += 1
            if r.label not in steps[key]:
                steps[key].append(r.label)
    if not counts:
        return []
    out = [
        "",
        "### Differences no divergence covers, by endpoint and path",
        "",
        "| endpoint | path | count | steps |",
        "|---|---|---|---|",
    ]
    for (endpoint, path), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        names = steps[(endpoint, path)]
        shown = ", ".join(f"`{name}`" for name in names[:3])
        if len(names) > 3:
            shown += f" and {len(names) - 3} more"
        out.append(f"| `{endpoint}` | `{path}` | {n} | {shown} |")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("php", type=Path, help="the PHP leg's steps directory")
    parser.add_argument("django", type=Path, help="the Django leg's steps directory")
    parser.add_argument("--labels", default="php,django")
    parser.add_argument("--summary", type=Path, help="append the report here too")
    parser.add_argument("--now", type=datetime.fromisoformat, default=None)
    args = parser.parse_args(argv)
    labels = tuple(args.labels.split(",", 1))
    php, django = read_leg(args.php), read_leg(args.django)
    results = judge(php, django, args.now)
    if not php or not django:
        print("A leg recorded no steps: see its suite.log.", file=sys.stderr)
        return 1
    text = render(results, labels)
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text + "\n")
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    import os

    import django

    # The registry fills as the handler modules load, in legacy_api's ready().
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "derbynet.settings")
    django.setup()
    sys.exit(main())
