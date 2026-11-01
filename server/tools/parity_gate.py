"""The parity gate's verdict (#37): compare a PHP leg and a Django leg of the
shell suite, step by step.

    uv run python -m tools.parity_gate PHP_STEPS DJANGO_STEPS
    uv run python -m tools.parity_gate --summary "$GITHUB_STEP_SUMMARY" A B

Each leg is `testing/exercise-one-database.sh` run with `PARITY_DIR` set
(`testing/steps.sh`, `testing/docker/run-suite.sh parity`): a `steps.tsv`
with one line per step, each step's slice of `output.curl`, and a `complete`
file the leg writes when it reaches the end. The legs run the same steps in
the same order, so a step is paired with the step of the same label. Ids are
numbered by first appearance across the whole leg, step after step, and
afresh after each database reset, so a row keeps its number from one step to
the next.

The gate fails when:

- a leg has no `complete` file, or a count in it other than its steps: the
  leg was cut short.
- a PHP step fails or is skipped. PHP is the reference, and `php-suite`
  passes it whole.
- a Django step is skipped, unless `SKIPS` lists it; or `SKIPS` lists a
  step Django ran.
- a Django step fails, or its requests part from PHP's, unless the step is
  in `WAITING`, the steps that wait on another ticket: a waiting step may
  fail, and stop where PHP's run goes on, once it has sent the request it
  waits on.
- a reply differs from PHP's, in any step, waiting or not, unless the
  registry allows it.
- a step in `WAITING` passes on Django, a step in `STEP_DIVERGENCES` no
  longer parts, or a step in `AFTER_SKIP` matches PHP. Remove the entry.
- a step ran in one leg and not in the other.

A step in `AFTER_SKIP` shows the state of a step Django skipped, so its
replies may differ. A difference in a reply is allowed when the registry's
`PARITY_PATHS` covers its endpoint, path and values
(`legacy_api.registry.allowed_by`). A step in `STEP_DIVERGENCES` asks the two
servers different questions on purpose, so its transcripts are compared up
to where they part. Every allowed difference, waiting step, skip and retry
is printed, so nothing is hidden.
"""

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools.transcript_diff import (
    MISSING,
    Difference,
    Report,
    absorb,
    diff,
    new_runs,
    parse,
)

_INDEX = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class Waiting:
    """A step that waits on another ticket. `request` is the request line
    (its start) PHP's run sends that Django can't answer yet: Django may stop
    only after sending it. None for a step that records no requests, such as
    a Puppeteer test."""

    ticket: str
    request: str | None = None

    def __str__(self):
        return self.ticket


# The Django leg's steps that wait on another ticket, by label. Remove an
# entry when its ticket lands; the gate fails while a listed step passes, so
# the list can't go stale. A waiting step may fail and stop early, but only
# after its request, and a reply it gets is still compared.
_PAGES = (
    "the legacy pages: #40, #41, #42, #43, #44, #45, #46, #47, #48, #49, #50,"
    " #52, #53, #54"
)
# test-visit-each-page.sh opens about.php first.
_EACH_ROLE = Waiting(f"{_PAGES} (test-visit-each-page.sh)", "about.php")
WAITING: dict[str, Waiting] = {
    "test-each-role.sh": _EACH_ROLE,
    # The page checks, which record no request, follow the first logout.
    "test-permissions.sh": Waiting(
        f"{_PAGES} (fixtures/role-pages.json)", "post action.php action=role.login"
    ),
    "test-basic-racing.sh": Waiting("#49 (history.php)", "history.php"),
    "puppeteer_test all-pages-test.js": Waiting(_PAGES),
    "puppeteer_test coordinator-test.js": Waiting("#41 (coordinator.php)"),
    "puppeteer_test ondeck-columns-test.js": Waiting("#52 (ondeck.php)"),
    "test-each-role.sh (2)": _EACH_ROLE,
    "test-each-role.sh (3)": _EACH_ROLE,
    "puppeteer_test checkin-empty-test.js": Waiting("#42 (checkin.php)"),
    "test-each-role.sh (4)": _EACH_ROLE,
    "test-each-role.sh (5)": _EACH_ROLE,
    "test-each-role.sh (6)": _EACH_ROLE,
}

# The steps the Django leg skips, with the reason. The PHP leg skips none.
SKIPS: dict[str, str] = {
    "test-balloting.sh": "#54 (D7): balloting moved to the backlog",
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
    waiting: Waiting | None = None
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
    waiting: dict[str, Waiting] | None = None,
    step_divergences: dict[str, str] | None = None,
    allow=_allow,
    after_skip: dict[str, str] | None = None,
    skips: dict[str, str] | None = None,
) -> list[StepResult]:
    waiting = WAITING if waiting is None else waiting
    after_skip = AFTER_SKIP if after_skip is None else after_skip
    skips = SKIPS if skips is None else skips
    step_divergences = (
        STEP_DIVERGENCES if step_divergences is None else step_divergences
    )
    # One numbering per leg, carried from step to step.
    runs = new_runs(now)
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
        if step.outcome == "skip":
            result.problems.append("skipped on PHP, which skips nothing")
        if other is None:
            result.problems.append("the Django leg never ran it")
        elif other.outcome == "skip" and step.label not in skips:
            result.problems.append("skipped on Django, and SKIPS doesn't list it")
        elif other.outcome != "skip" and step.label in skips:
            result.problems.append(
                "SKIPS lists it but Django ran it: remove it from SKIPS"
            )
        if other is None or "skip" in (step.outcome, other.outcome):
            # Number the ids the step that ran shows, so the next steps
            # carry on from them.
            for leg_step, run in ((step, runs[0]), (other, runs[1])):
                if leg_step is not None and leg_step.outcome != "skip":
                    absorb(parse(_read(leg_step.transcript)), run)
            continue
        report = diff(_read(step.transcript), _read(other.transcript), allow, now, runs)
        result.report = report
        _judge_step(result, report, other, step_divergences)
    for label, other in by_label.items():
        results.append(
            StepResult(label, None, other, problems=["the PHP leg never ran it"])
        )
    for label in [*waiting, *skips]:
        if not any(r.label == label for r in results):
            results.append(
                StepResult(label, None, None, problems=["is listed but no leg ran it"])
            )
    return results


def _first_request(transcript: str, start: str) -> int | None:
    """The index of the first exchange whose request line starts so."""
    for exchange in parse(transcript):
        if exchange.request.startswith(start):
            return exchange.index
    return None


def _judge_step(
    result: StepResult,
    report: Report,
    other: Step,
    step_divergences: dict[str, str],
) -> None:
    label = result.label
    if label in step_divergences:
        if report.diverged:
            result.diverges_by = step_divergences[label]
        else:
            result.problems.append(
                "no longer parts from PHP's requests: remove it from"
                " STEP_DIVERGENCES in server/tools/parity_gate.py"
            )
    # What a waiting step may do: fail on Django, and so stop sending
    # requests where PHP's run goes on, after the request it waits on.
    excused = []
    if other.outcome == "fail":
        excused.append("fails on Django")
    parted = report.diverged is not None and result.diverges_by is None
    stopped = parted and report.diverged.b is MISSING
    if stopped:
        excused.append("stops before PHP's run ends")
        waited = result.waiting.request if result.waiting else None
        if waited is not None:
            at = _first_request(_read(result.php.transcript), waited)
            if at is None or report.diverged.index <= at:
                result.problems.append(
                    f"stops before it sends {waited}, which it waits on"
                )
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


def leg_cut_short(directory: Path, steps: list[Step]) -> str | None:
    """Why a leg didn't run to the end, or None. `testing/steps.sh` writes
    the number of steps to `complete` when the run reaches its end."""
    complete = directory / "complete"
    if not complete.exists():
        return f"{directory} has no complete file: the leg was cut short"
    count = complete.read_text().strip()
    if count != str(len(steps)):
        return f"{directory} ran {count} steps but recorded {len(steps)}"
    return None


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
    short = [
        reason
        for directory, steps in ((args.php, php), (args.django, django))
        if (reason := leg_cut_short(directory, steps))
    ]
    text = render(results, labels)
    if short:
        text = "\n".join(f"**FAIL: {reason}.**" for reason in short) + "\n\n" + text
    print(text)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text + "\n")
    return 1 if short or any(r.failed for r in results) else 0


if __name__ == "__main__":
    import os

    import django

    # The registry fills as the handler modules load, in legacy_api's ready().
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "derbynet.settings")
    django.setup()
    sys.exit(main())
