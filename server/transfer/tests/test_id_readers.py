"""`transfer/` reads every legacy id through `transfer.legacy.values` (#231 retro).

#231's first version wrote a fifth reader for the ids inside kiosk
parameters, copied the strictness of Django's kiosk reader, and so treated
`"5"`, which a PHP on MySQL writes, as an id that names no row. The import
already had `values.integer()` for columns and `action_log`'s reader for
ids inside JSON; four readers then disagreed on `" 7"`, `"7.0"` and `true`.

This test finds a number parsed anywhere else in `transfer/`: a call to
`int()` or `float()`, a `str.isdigit()` and its kin, or a regex digit class.
Read an id in JSON or text with `values.legacy_id()`, and a column value
with `values.integer()`, `ref_id()` or `integer_or_none()`. A parse that
reads no legacy id goes in `NOT_IDS` with its reason.
"""

import ast
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
VALUES = "transfer/legacy/values.py"

# (file, the call or pattern as `ast.unparse` writes it): why it is no id.
NOT_IDS = {
    ("transfer/export.py", "int(value)"): "a bool from a Django field, to 0 or 1",
    ("transfer/export.py", "int(moment.timestamp() * 1000)"): "a time",
    ("transfer/export.py", "int(moment.timestamp())"): "a time",
    ("transfer/legacy/source.py", "int(rows[-1]['itemvalue'])"): (
        "the schema version; text that is no number refuses the source"
    ),
    ("transfer/snapshot.py", "int(racer.passed_inspection)"): "a Django bool",
    ("transfer/snapshot.py", "int(racer.exclude)"): "a Django bool",
    ("transfer/snapshot.py", "int(p.bucketed)"): "a Django bool",
    ("transfer/snapshot.py", "int(p.continue_racing)"): "a Django bool",
    ("transfer/legacy/raceinfo.py", "'tt-(lane|place)(\\\\d+)'"): (
        "a lane or place number in a RaceInfo key"
    ),
}

_DIGIT_METHODS = {"isdigit", "isdecimal", "isnumeric"}
_DIGIT_PATTERN = re.compile(r"\\d|\[0-9\]")


def parsers(source: str) -> list[tuple[int, str]]:
    """Each number parse in `source`, as (line, unparsed code)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in {"int", "float"}) or (
                isinstance(func, ast.Attribute) and func.attr in _DIGIT_METHODS
            ):
                found.append((node.lineno, ast.unparse(node)))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _DIGIT_PATTERN.search(node.value)
        ):
            found.append((node.lineno, ast.unparse(node)))
    return found


def _modules() -> list[Path]:
    return [
        path
        for path in sorted(SERVER.glob("transfer/**/*.py"))
        if not {"tests", "migrations"} & set(path.relative_to(SERVER).parts)
        and path.relative_to(SERVER).as_posix() != VALUES
    ]


def test_transfer_reads_no_number_outside_values():
    stray = []
    seen = set()
    for path in _modules():
        name = path.relative_to(SERVER).as_posix()
        for line, code in parsers(path.read_text()):
            seen.add((name, code))
            if (name, code) not in NOT_IDS:
                stray.append(f"{name}:{line} {code}")
    assert stray == [], (
        "read a legacy id with transfer.legacy.values (legacy_id for JSON or"
        " text, integer for a column), or add the parse to NOT_IDS with why"
        " it reads no id"
    )
    assert set(NOT_IDS) <= seen, f"stale NOT_IDS entries: {set(NOT_IDS) - seen}"


# #231's first reader for kiosk parameter ids.
READER_231 = """
def _whole_number(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None
"""


def test_finds_the_reader_231_wrote():
    assert sorted(code for _, code in parsers(READER_231)) == [
        "int(value)",
        "value.isdigit()",
    ]


def test_finds_a_regex_digit_class_and_float():
    source = "re.fullmatch(r'[0-9]+', text)\nre.compile(r'id(\\d+)')\nfloat(text)\n"
    assert sorted(code for _, code in parsers(source)) == [
        "'[0-9]+'",
        "'id(\\\\d+)'",
        "float(text)",
    ]
