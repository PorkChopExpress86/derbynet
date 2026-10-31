"""Reading the PHP golden fixtures and the deviation list (D25).

See `tests/golden/README.md` for the fixture formats and #20, "How fixtures
record decisions", for the deviation rules.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "golden"
DEVIATIONS_FILE = GOLDEN_DIR / "deviations.json"

# The decisions of #20, "Quirk decisions". Only a Fix may have a deviation.
QUIRK_DECISIONS = {
    "Q1": "fix",
    "Q2": "fix",
    "Q3": "reproduce",
    "Q4": "fix",
    "Q5": "fix",
    "Q6": "reproduce",
    "Q7": "fix",
    "Q8": "fix",
    "Q9": "fix",
    "Q10": "reproduce",
    "Q11": "fix",
    "Q12": "fix",
    "Q13": "fix",
    "Q14": "fix",
    "Q15": "fix",
    "Q16": "fix",
    "Q17": "reproduce",
    "Q18": "fix",
    "Q19": "fix",  # D30
    "Q20": "fix",
    "Q21": "fix",
    "Q22": "fix",
    "Q23": "fix",
    "Q24": "fix",
    "Q25": "fix",
    # Rows of #64 and owner decisions that change a recorded PHP exchange (#29).
    "C14": "fix",
    "C21": "fix",
    "D5": "fix",
    "D34": "fix",
    "S23": "fix",
    # Accepted in D51 and D60 (#29): R1 an unknown racer id is refused, R2 a
    # car number must be a whole number.
    "R1": "fix",
    "R2": "fix",
    # Rows of #64 that a #31 endpoint fixes.
    "C23": "fix",
    "C24": "fix",
    "S17": "fix",
    # Rows of #64 that the #36 snapshot and setup endpoints fix.
    "S5": "fix",
    "S6": "fix",
    "S7": "fix",
    "S25": "fix",
    # Rows of #64 and owner decisions that the #34 kiosk, scene, playlist,
    # message and replay endpoints fix.
    "C18": "fix",
    "C19": "fix",
    "C20": "fix",
    "D16": "fix",
    "S18": "fix",
    "S27": "fix",
    # Accepted in D51 (#31): R3 no current heat reads
    # null, R5 result.write fails in JSON, not XML, R6 no scheduler debug
    # output, R7 the generate payload shows the state the request left, R8
    # poll.results starts with no leading space, R9 poll.results with
    # details names each column once.
    "R3": "fix",
    "R5": "fix",
    "R6": "fix",
    "R7": "fix",
    "R8": "fix",
    "R9": "fix",
    # Accepted in D58 (#33): A1 a pack-wide award lists
    # classid and rankid 0, never null; A2, A4 and A5 refuse a class, a
    # full-field-calc or an award key PHP would store unchecked; A3
    # award.present with no parameters answers success, as #33 asks.
    "A1": "fix",
    "A2": "fix",
    "A3": "fix",
    "A4": "fix",
    "A5": "fix",
    # Accepted in D58 (#34): R10 deleting a scene clears
    # what names it, R11 a step of another event's class fails no_classid,
    # R12 a scene id or name of no scene of the event changes nothing.
    "R10": "fix",
    "R11": "fix",
    "R12": "fix",
    # Rows of #64 that the #35 photo, video and slide endpoints fix.
    "C17": "fix",
    "C27": "fix",
    "S8": "fix",
    "S10": "fix",
    "S19": "fix",
    "S20": "fix",
    # Accepted in D61 (#35): P1 renders apply the EXIF orientation and keep
    # the upload (#8, #73), P2 render sizes are allowlisted, P3 an upload is
    # decoded and its name and size checked, P4 autocrop runs OpenCV and
    # reports a miss (#74), P5 an unknown repo fails, P6 a bad crop or rotate
    # fails instead of a PHP fatal error, P7 an empty photo column counts as
    # no photo in slide.next.
    "P1": "fix",
    "P2": "fix",
    "P3": "fix",
    "P4": "fix",
    "P5": "fix",
    "P6": "fix",
    "P7": "fix",
}


def canonical(record) -> str:
    """The text `cj()` in `capture-engine.php` hashes: compact JSON."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def sha256(record) -> str:
    return hashlib.sha256(canonical(record).encode()).hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    """`{case id: sha256}` from a `manifest*.tsv`."""
    rows = {}
    for line in path.read_text().splitlines():
        case_id, _nheats, digest = line.split("\t")
        rows[case_id] = digest
    return rows


def ordered_manifest() -> dict[str, str]:
    rows = {}
    for path in sorted((GOLDEN_DIR / "engine" / "ordered").glob("manifest-*.tsv")):
        rows |= read_manifest(path)
    return rows


def rotation_manifest() -> dict[str, str]:
    return read_manifest(GOLDEN_DIR / "engine" / "rotation" / "manifest.tsv")


def read_cases(path: Path) -> dict[str, dict]:
    """Records from a file `write_cases()` made, with `heats` back in place,
    or from one `write_json_lines()` made, as they are."""
    cases = {}
    for case_id, item in json.loads(path.read_text()).items():
        if "meta" not in item:
            cases[case_id] = item
            continue
        record = dict(item["meta"])
        if "heats" in item:
            record["heats"] = item["heats"]
        # Restore cj()'s key order: failure, nheats, heats, then the rest.
        order = ["failure", "nheats", "ncars", "heats"]
        cases[case_id] = {k: record[k] for k in order if k in record} | {
            k: v for k, v in record.items() if k not in order
        }
    return cases


def read_transcript(path: Path) -> dict[str, dict]:
    """`{exchange name: {header: value, ..., "body": bytes}}` from a `.http`
    file, in the format `capture/transcript.mjs` documents."""
    data = path.read_bytes()
    exchanges = {}
    pos = 0
    while pos < len(data):
        end = data.index(b"\nbody:\n", pos)
        lines = data[pos:end].decode().split("\n")
        exchange = {}
        for line in lines[1:]:
            key, _, value = line.partition(": ")
            exchange.setdefault(key, value)
        start = end + len(b"\nbody:\n")
        length = int(exchange["length"])
        exchange["body"] = data[start : start + length]
        if data[start + length : start + length + 1] != b"\n":
            raise ValueError(f"{path}: exchange {lines[0]} has the wrong length")
        exchanges[lines[0].removeprefix("### ")] = exchange
        pos = start + length + 1
    return exchanges


def deviations() -> list[dict]:
    return json.loads(DEVIATIONS_FILE.read_text())["deviations"]


Claim = set[tuple[str, str]] | Callable[[str, str], bool]
"""The `deviations.json` entries one writer owns, by (quirk, fixture): a set
of pairs, or a test of a pair for a writer whose pairs depend on its run."""

# Entries recorded by hand, which no writer rewrites.
HAND_RECORDED: Claim = {
    ("Q1", "engine/out-of-range.json"),
    ("Q1", "http/schedule/cant-fulfill-4-lanes.http"),
    ("Q1", "http/schedule/seven-lanes.http"),
    ("Q1", "http/schedule/more-than-200-cars.http"),
    ("Q4", "http/suite/basic-racing.http"),
}


def claims(claim: Claim, entry: dict) -> bool:
    pair = (entry["quirk"], entry["fixture"])
    return pair in claim if isinstance(claim, set) else claim(*pair)


def deviations_text(claim: Claim, entries: list[dict]) -> str:
    """`deviations.json` with the entries `claim` owns replaced by `entries`.

    An entry written again keeps its place and one no longer written goes, so
    the writers can run in any order and leave the same bytes. A new entry goes
    after the last entry whose quirk number is not above its own."""
    listing = json.loads(DEVIATIONS_FILE.read_text())
    new = {(e["quirk"], e["fixture"]): e for e in entries}
    assert len(new) == len(entries), "two entries for one (quirk, fixture)"
    foreign = [key for key, e in new.items() if not claims(claim, e)]
    assert not foreign, f"entries outside the writer's claim: {foreign}"
    kept = []
    for entry in listing["deviations"]:
        if not claims(claim, entry):
            kept.append(entry)
        elif (key := (entry["quirk"], entry["fixture"])) in new:
            kept.append(new.pop(key))
    for entry in new.values():
        number = int(entry["quirk"][1:])
        at = max(
            (i + 1 for i, e in enumerate(kept) if int(e["quirk"][1:]) <= number),
            default=0,
        )
        kept.insert(at, entry)
    listing["deviations"] = kept
    return json.dumps(listing, indent=2, ensure_ascii=False) + "\n"


def write_deviation_entries(claim: Claim, entries: list[dict]) -> None:
    """Write `deviations_text(claim, entries)` to `deviations.json`."""
    DEVIATIONS_FILE.write_text(deviations_text(claim, entries))


def engine_deviations(fixture: str) -> dict[str, dict]:
    """`{case id: expected Django record}` for an engine fixture."""
    expected = {}
    for entry in deviations():
        if entry["fixture"] == fixture:
            records = json.loads((GOLDEN_DIR / entry["expected"]).read_text())
            for case_id in entry["cases"]:
                expected[case_id] = records[case_id]
    return expected


def transcript_deviations(fixture: str) -> dict[str, tuple[str, dict]]:
    """`{exchange name: (quirk, expected Django exchange)}` for a transcript."""
    expected = {}
    for entry in deviations():
        if entry["fixture"] == fixture:
            exchanges = read_transcript(GOLDEN_DIR / entry["expected"])
            for name in entry["exchanges"]:
                expected[name] = (entry["quirk"], exchanges[name])
    return expected
