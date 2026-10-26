"""Write the Q20 and Q21 deviation entries for the reschedule transcripts.

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

An expected exchange is PHP's with the `chart` replaced. For Q21 the reply
becomes PHP's success reply: the chart, the `timing` keys, and the
coordinator poll payload of the exchange before it, which the dry run did not
change. The rest of each reply is PHP's. The poll payload of a Q20 reply can
differ too (the current heat's racers, the heat counts), which #31 records
when it ports the handler.
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
    php_changed_a_kept_heat,
    replay,
    replay_quirks,
)

REASONS = {
    "Q20": (
        "D34: Django keeps the running heat and the staged heat as they are"
        " and fits the racer into later or added heats; PHP can change both."
        " Only the chart is Django's; #31 completes the rest of each reply."
    ),
    "Q21": (
        "PHP never searches with the last heat it adds and answers nochart;"
        " Django searches with it and fits the racer in."
    ),
}


def php_json(value) -> str:
    """`json_encode(..., JSON_PRETTY_PRINT)` as PHP writes it."""
    return json.dumps(value, indent=4).replace("/", "\\/") + "\n"


def chart_block(chart) -> str:
    lines = ",\n".join(f"        {json.dumps(heat)}" for heat in chart)
    return f'"chart": [\n{lines}\n    ]'


def with_chart(body: str, chart) -> str:
    start = body.index('"chart": [')
    end = body.index("\n    ]", start) + len("\n    ]")
    return body[:start] + chart_block(chart) + body[end:]


def success_reply(php: dict, previous: dict, chart, timing_keys) -> str:
    """PHP's success reply for a dry run that PHP answered with nochart."""
    assert previous["request"].startswith("GET action.php?query=poll.coordinator")
    failed = json.loads(php["body"])
    poll = json.loads(previous["body"])
    reply = {
        "action": failed["action"],
        "outcome": {"summary": "success", "code": "success", "description": ""},
        **{k: v for k, v in failed.items() if k not in ("action", "outcome")},
        "chart": chart,
        "timing": {
            key: "<seconds>" for key in ("load", "inject", "update", "master-heat")
        }
        | {
            "internal": {
                key: True if key.endswith("-timed-out") else "<seconds>"
                for key in timing_keys
            }
        },
        **poll,
    }
    text = php_json(reply)
    assert php_json(poll) == previous["body"].decode(), "php_json is not PHP's"
    return text


def exchange_text(name: str, exchange: dict, body: str) -> str:
    data = body.encode()
    headers = "".join(
        f"{key}: {value}\n"
        for key, value in exchange.items()
        if key not in ("body", "length", "note")
    )
    return f"### {name}\n{headers}length: {len(data)}\nbody:\n{body}\n"


def expected_files() -> tuple[dict[Path, str], list[dict]]:
    replies = [((SUITE_FIXTURE, name), r) for name, r in replay(True)]
    replies += replay_quirks(True)
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
        if reply.chart == php_chart:
            continue
        if php_chart is None:
            quirk = "Q21"
            previous = list(exchanges.values())[list(exchanges).index(name) - 1]
            text = success_reply(php, previous, reply.chart, reply.timing_keys)
        elif php_changed_a_kept_heat(php_chart, reply.kept):
            quirk = "Q20"
            text = with_chart(php["body"].decode(), reply.chart)
        else:
            raise SystemExit(
                f"{fixture} {name}: Django's chart differs from PHP's, and"
                " PHP kept the running and staged heats, so D34 does not explain it"
            )
        files.setdefault((quirk, fixture), []).append(exchange_text(name, php, text))
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
    ours = {"Q20", "Q21"}
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
