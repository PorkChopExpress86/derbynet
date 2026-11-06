"""The parity gate's verdict (#37): compare a PHP leg and a Django leg of the
shell suite, step by step.

    uv run python -m tools.parity_gate PHP_STEPS DJANGO_STEPS
    uv run python -m tools.parity_gate --summary "$GITHUB_STEP_SUMMARY" A B
    uv run python -m tools.parity_gate --failing failing.json A B

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
(`legacy_api.registry.allowed_by`), or, in one step only, when that step's
`STEP_PATHS` entry does. A step in `STEP_DIVERGENCES` asks the two
servers different questions on purpose, so its transcripts are compared up
to where they part. Every allowed difference, waiting step, skip and retry
is printed, so nothing is hidden.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from legacy_api.parity_paths import ParityPath
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
SKIPS: dict[str, str] = {}

# The steps whose requests differ between the legs on purpose, because the
# script asks Django other questions when DERBYNET_BACKEND=django. Their
# transcripts are compared up to where the requests part.
STEP_DIVERGENCES: dict[str, str] = {
    "test-reschedule.sh": (
        "D34, D39: with DERBYNET_BACKEND=django the script checks Django's"
        " charts and refusals, and races other heats (testing/README.md)"
    ),
}

# Reply paths that may differ from PHP's in one step only, because a divergence
# changes the state the step builds, and so the replies that show that state,
# rather than one endpoint's reply. Each entry cites an id of the endpoint's
# divergences or of the step's STEP_DIVERGENCES entry (`step_path_text`).
_COORDINATOR = (
    "action schedule.reschedule",
    "action heat.select",
    "query poll.coordinator",
)


def _paths(endpoints, paths, divergence, pairs) -> tuple[ParityPath, ...]:
    """A ParityPath for each endpoint, path and (php, django) value pair."""
    return tuple(
        ParityPath(endpoint, path, divergence, php=php, django=django)
        for endpoint in endpoints
        for path in paths
        for php, django in pairs
    )


# The racers of a heat: another heat's racers, or a racer one heat lacks.
_HEAT_RACERS = (
    (("$.racers[*]",), ((MISSING, dict), (dict, MISSING))),
    (
        ("$.racers[*].racerid", "$.racers[*].carnumber", "$.racers[*].lane"),
        ((int, int),),
    ),
    (("$.racers[*].name",), ((str, str),)),
)

STEP_PATHS: dict[str, tuple[ParityPath, ...]] = {
    # Django keeps the running and the staged heat and adds heats at the end
    # (D34, Q20), and refuses a racer who is not on the roster (D39, Q25), so
    # its charts differ from PHP's, and with them the heat counts and the
    # racers each later reply shows. PHP turns racing off when it squeezes a
    # heat out of the running round; Django never changes that heat (D39). The
    # timing keys name the searches each server ran: Django tries one more
    # with its last added heat where PHP's search times out (Q21); the golden
    # replays check them (deviations.json, Q20 and Q21).
    "test-reschedule.sh": (
        *_paths(
            ("action schedule.reschedule",),
            ("$.chart[*]",),
            "Q20",
            (
                (str, str),
                (MISSING, str),
                (str, MISSING),
                # A heat number the chart skips, as its renumbering leaves it.
                (None, str),
                (str, None),
            ),
        ),
        *_paths(
            ("action schedule.reschedule",),
            ("$.timing.internal.run-*-extra-*-start",),
            "Q21",
            ((MISSING, "<seconds>"),),
        ),
        *_paths(
            ("action schedule.reschedule",),
            ("$.timing.internal.run-*-timed-out",),
            "Q21",
            ((True, MISSING),),
        ),
        *(
            allowed
            for paths, pairs in _HEAT_RACERS
            for allowed in _paths(_COORDINATOR, paths, "D34", pairs)
        ),
        *_paths(
            _COORDINATOR,
            ("$.current-heat.number-of-heats", "$.rounds[*].heats_scheduled"),
            "D34",
            ((int, int),),
        ),
        *_paths(_COORDINATOR, ("$.current-heat.now_racing",), "D39", ((False, True),)),
    ),
    # Under the master schedule, PHP's reply to schedule.generate shows the
    # current heat and its racers from before it numbers the master heats and
    # moves to the next-up heat; Django's shows the next-up heat, numbered,
    # which has no result yet (R7, D51).
    "test-master-schedule.sh": (
        *_paths(
            ("action schedule.generate",),
            (
                "$.current-heat.classid",
                "$.current-heat.round",
                "$.current-heat.tbodyid",
                "$.current-heat.heat",
            ),
            "R7",
            ((None, int), (int, int)),
        ),
        *_paths(
            ("action schedule.generate",),
            (
                "$.current-heat.roundid",
                "$.current-heat.number-of-heats",
                "$.current-heat.max_masterheat",
            ),
            "R7",
            ((int, int),),
        ),
        *_paths(
            ("action schedule.generate",),
            ("$.current-heat.masterheat",),
            "R7",
            ((False, int), (int, int)),
        ),
        *_paths(
            ("action schedule.generate",),
            ("$.current-heat.class",),
            "R7",
            ((str, str),),
        ),
        *(
            allowed
            for paths, pairs in _HEAT_RACERS
            for allowed in _paths(("action schedule.generate",), paths, "R7", pairs)
        ),
        *_paths(
            ("action schedule.generate",), ("$.racers[*].note",), "R7", ((str, str),)
        ),
        *_paths(
            ("action schedule.generate",),
            ("$.racers[*].finishtime", "$.racers[*].finishplace"),
            "R7",
            ((float, ""), (int, "")),
        ),
    ),
}


def step_path_text(
    label: str, allowed: ParityPath, step_divergences: dict[str, str]
) -> str | None:
    """The divergence a step path cites: the endpoint's entry with its id, or
    else the step's STEP_DIVERGENCES entry when that names the id."""
    from legacy_api.registry import divergence_text

    text = divergence_text(allowed.endpoint, allowed.divergence)
    if text:
        return text
    reason = step_divergences.get(label, "")
    ids = reason.split(": ", 1)[0].split(", ")
    return f"{label}: {reason}" if allowed.divergence in ids else None


# The steps after a skipped step whose replies show the state that step would
# have left, up to the next reset. Their replies may differ from PHP's; their
# requests and outcomes are still judged.
AFTER_SKIP: dict[str, str] = {}

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
    step_paths: dict[str, tuple[ParityPath, ...]] | None = None,
) -> list[StepResult]:
    waiting = WAITING if waiting is None else waiting
    after_skip = AFTER_SKIP if after_skip is None else after_skip
    skips = SKIPS if skips is None else skips
    step_divergences = (
        STEP_DIVERGENCES if step_divergences is None else step_divergences
    )
    step_paths = STEP_PATHS if step_paths is None else step_paths
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
        step_allow = _with_step_paths(
            step.label, step_paths.get(step.label, ()), step_divergences, allow
        )
        report = diff(
            _read(step.transcript), _read(other.transcript), step_allow, now, runs
        )
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


def _with_step_paths(label, paths, step_divergences, allow):
    """`allow`, and the step's own STEP_PATHS before it."""
    if not paths:
        return allow

    def check(difference):
        for allowed in paths:
            if allowed.covers(
                difference.endpoint, difference.path, difference.a, difference.b
            ):
                text = step_path_text(label, allowed, step_divergences)
                if text:
                    return text
        return allow(difference)

    return check


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


def failing(results: list[StepResult], short: list[str]) -> dict:
    """The steps that fail the gate, for `tools/parity_tracking.py`: each
    one's label, its count of differences from PHP, the WAITING entry it
    waits on, and its problems. `short` holds why a leg was cut short."""
    return {
        "short": short,
        "steps": [
            {
                "label": r.label,
                "differences": len(r.report.differences) if r.report else 0,
                "waiting": str(r.waiting) if r.waiting else None,
                "problems": r.problems,
            }
            for r in results
            if r.failed
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("php", type=Path, help="the PHP leg's steps directory")
    parser.add_argument("django", type=Path, help="the Django leg's steps directory")
    parser.add_argument("--labels", default="php,django")
    parser.add_argument("--summary", type=Path, help="append the report here too")
    parser.add_argument(
        "--failing", type=Path, help="write the failing steps here, as JSON"
    )
    parser.add_argument("--now", type=datetime.fromisoformat, default=None)
    args = parser.parse_args(argv)
    labels = tuple(args.labels.split(",", 1))
    php, django = read_leg(args.php), read_leg(args.django)
    results = judge(php, django, args.now)
    if not php or not django:
        print("A leg recorded no steps: see its suite.log.", file=sys.stderr)
        if args.failing:
            args.failing.write_text(
                json.dumps(failing([], ["a leg recorded no steps"]), indent=1)
            )
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
    if args.failing:
        args.failing.write_text(json.dumps(failing(results, short), indent=1))
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
