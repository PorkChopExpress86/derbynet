"""Reading the PHP golden fixtures and the deviation list (D25).

See `tests/golden/README.md` for the fixture formats and #20, "How fixtures
record decisions", for the deviation rules.
"""

import hashlib
import json
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
    # Proposed in #29, for the owner to confirm: R1 an unknown racer id is
    # refused, R2 a car number must be a whole number.
    "R1": "fix",
    "R2": "fix",
    # Proposed in #31, for the owner to confirm: R3 no current heat reads
    # null, R6 no scheduler debug output, R7 the generate payload shows the
    # state the request left.
    "R3": "fix",
    "R6": "fix",
    "R7": "fix",
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
