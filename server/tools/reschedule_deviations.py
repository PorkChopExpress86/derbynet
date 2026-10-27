"""Write the Q20, Q21 and Q25 deviation entries for the reschedule transcripts.

    uv run python tools/reschedule_deviations.py            # write them
    uv run python tools/reschedule_deviations.py --check    # fail if they differ

The engine replay in `racing/engine/tests/reschedule_replay.py` gives the
chart Django replies with for every recorded `schedule.reschedule`. Each
exchange whose chart differs from PHP's gets an expected exchange under
`tests/golden/deviations/<quirk>/`, and `deviations.json` gets one entry per
quirk and fixture:

- Q20 (D34): Django keeps the running heat and the staged heat.
- Q21: PHP answers `nochart` where one more search with its last added heat
  fits the racer in.
- Q25 (D39): Django refuses to fit in a racer who is not on the round's
  roster, with `notonroster`. Its expected reply is `{"failure": code}`.

An expected exchange keeps PHP's request and holds only what the reschedule
service computes: the chart and the `timing.internal` keys
(`reschedule_replay.django_reply`). The outcome, the coordinator poll payload
and the HTTP headers belong to the handler, which #31 ports and records.
"""

import argparse
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from racing.engine.tests.golden import (  # noqa: E402
    DEVIATIONS_FILE,
    GOLDEN_DIR,
    read_transcript,
)
from racing.engine.tests.reschedule_replay import (  # noqa: E402
    SUITE_FIXTURE,
    engine,
    php_changed_a_kept_heat,
    replay,
    replay_quirks,
)

REASONS = {
    "Q20": (
        "D34: Django keeps the running heat and the staged heat as they are"
        " and fits the racer into later or added heats; PHP can change both."
        " The expected reply holds only the chart and timing keys the service"
        " computes; #31 records the rest."
    ),
    "Q21": (
        "PHP never searches with the last heat it adds and answers nochart;"
        " Django searches with it and fits the racer in. The expected reply"
        " holds only the chart and timing keys the service computes; #31"
        " records the rest."
    ),
    "Q25": (
        "D39: Django refuses to fit in a racer who is not on the round's"
        " roster and answers notonroster; PHP fits the racer in. The expected"
        " reply holds only the service's failure code; #31 records the rest."
    ),
}


def exchange_text(name: str, exchange: dict, reply: dict) -> str:
    body = json.dumps(reply, indent=4)
    request = "".join(
        f"{key}: {exchange[key]}\n" for key in ("request", "request-body")
    )
    return f"### {name}\n{request}length: {len(body.encode())}\nbody:\n{body}\n"


def expected_files() -> tuple[dict[Path, str], list[dict]]:
    replies = [((SUITE_FIXTURE, name), r) for name, r in replay(engine(True))]
    replies += replay_quirks(engine(True))
    files: dict[tuple[str, str], list[str]] = {}
    names: dict[tuple[str, str], list[str]] = {}
    transcripts = {}
    for (fixture, name), reply in replies:
        if fixture not in transcripts:
            transcripts[fixture] = read_transcript(GOLDEN_DIR / fixture)
        exchanges = transcripts[fixture]
        php = exchanges[name]
        body = json.loads(php["body"])
        php_chart = body["chart"] if body["outcome"]["code"] == "success" else None
        if reply.failure is None and reply.chart == php_chart:
            continue
        if reply.failure == "notonroster":
            quirk = "Q25"
        elif php_chart is None:
            quirk = "Q21"
        elif php_changed_a_kept_heat(php_chart, reply.kept):
            quirk = "Q20"
        else:
            raise SystemExit(
                f"{fixture} {name}: Django's chart differs from PHP's, and"
                " PHP kept the running and staged heats, so D34 does not explain it"
            )
        expected = reply.django()
        files.setdefault((quirk, fixture), []).append(
            exchange_text(name, php, expected)
        )
        names.setdefault((quirk, fixture), []).append(name)
    written = {}
    entries = []
    for (quirk, fixture), texts in files.items():
        expected = f"deviations/{quirk}/{Path(fixture).name}"
        written[GOLDEN_DIR / expected] = "".join(texts)
        entries.append(
            {
                "quirk": quirk,
                "fixture": fixture,
                "exchanges": names[quirk, fixture],
                "reason": REASONS[quirk],
                "expected": expected,
            }
        )
    return written, entries


def merged_deviations(entries: list[dict]) -> str:
    current = json.loads(DEVIATIONS_FILE.read_text())
    ours = {"Q20", "Q21", "Q25"}
    kept = [e for e in current["deviations"] if e["quirk"] not in ours]
    return json.dumps({**current, "deviations": kept + entries}, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files, entries = expected_files()
    files[DEVIATIONS_FILE] = merged_deviations(entries)
    stale = [
        path
        for path, text in files.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    if args.check:
        for path in stale:
            print(f"out of date: {path.relative_to(GOLDEN_DIR)}", file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text := files[path], encoding="utf-8")
        print(f"wrote {path.relative_to(GOLDEN_DIR)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
