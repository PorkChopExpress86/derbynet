"""`ordinals.py` against `tests/golden/engine/ordinals.json`, every case.

A record holds the value and, where PHP read an undefined array key, its
`warnings`. The warnings are PHP's alone, so only the value is compared.
"""

import json
import re

from racing.engine.standings.ordinals import nth_fastest, ordinal

from .golden import GOLDEN_DIR

PHP = json.loads((GOLDEN_DIR / "engine" / "ordinals.json").read_text())
CALL = re.compile(r"(ordinal|nth_fastest)\((.*)\)")


def django_value(case_id):
    name, args = CALL.fullmatch(case_id).groups()
    args = json.loads(f"[{args}]")
    if name == "ordinal":
        return {"ordinal": ordinal(*args)}
    return {"nth_fastest": nth_fastest(*args)}


def test_every_case_matches_php():
    assert len(PHP) == 363
    mismatched = {}
    for case_id, record in PHP.items():
        php = {key: value for key, value in record.items() if key != "warnings"}
        django = django_value(case_id)
        if django != php:
            mismatched[case_id] = (django, php)
    assert mismatched == {}


def test_the_quirks_q10_keeps():
    assert ordinal(111) == "111st"
    assert ordinal(0) is None
    assert ordinal("T0") == "T"
    assert nth_fastest(0, "Den") == " Fastest in Den"
