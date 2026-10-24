"""The encoder against PHP 8.4's own output.

`data/php_encoding.json` is what `data/php_encoding.php` printed in the server
image. Regenerate it after changing the inputs, from the repository root, with
`testing/docker/run-suite.sh server` built (or any golden capture image):

    docker run --rm -v "$PWD/server/legacy_api/tests/data:/d:ro" \\
        --entrypoint php derbynet-test-server /d/php_encoding.php \\
        > server/legacy_api/tests/data/php_encoding.json
"""

import json
from pathlib import Path

import pytest

from legacy_api.encoding import encode_php_json, is_php_numeric

FIXTURE = json.loads((Path(__file__).parent / "data" / "php_encoding.json").read_text())
CASES = FIXTURE["cases"]


def _ids(kind):
    return [repr(c["input"]) for c in CASES if c["kind"] == kind]


def _cases(kind):
    return [c for c in CASES if c["kind"] == kind]


def test_the_fixture_comes_from_the_php_the_server_image_runs():
    assert FIXTURE["php"] == "8.4"
    assert FIXTURE["serialize_precision"] == "-1"


def test_the_fixture_covers_the_cases_the_ticket_names():
    strings = {c["input"] for c in _cases("string")}
    assert {
        "007", " 5", "5 ", "+5", "-0", ".5", "5.", "1.50", "1e3", "0x1A", "NaN",
        "INF", "9223372036854775807", "9223372036854775808", "",
    } <= strings  # fmt: skip
    values = {c["input"] for c in _cases("value")}
    assert {'{"007":"x"}', "[]", "{}"} <= values


@pytest.mark.parametrize("case", _cases("string"), ids=_ids("string"))
def test_a_string_is_numeric_exactly_when_php_says_so(case):
    assert is_php_numeric(case["input"]) is case["is_numeric"]


@pytest.mark.parametrize("case", _cases("string"), ids=_ids("string"))
def test_a_string_encodes_as_php_encodes_it(case):
    assert encode_php_json(case["input"]) == (case["json"] + "\n").encode()


@pytest.mark.parametrize("case", _cases("float"), ids=_ids("float"))
def test_a_float_encodes_as_php_encodes_it(case):
    assert encode_php_json(float(case["input"])) == (case["json"] + "\n").encode()


@pytest.mark.parametrize("case", _cases("value"), ids=_ids("value"))
def test_a_structure_encodes_as_php_encodes_it(case):
    value = json.loads(case["input"])
    assert encode_php_json(value) == (case["json"] + "\n").encode()


def test_keys_are_never_converted():
    assert encode_php_json({"007": "007", 5: "x"}) == (
        b'{\n    "007": 7,\n    "5": "x"\n}\n'
    )


def test_an_empty_list_and_an_empty_dict_stay_distinct():
    assert encode_php_json([]) == b"[]\n"
    assert encode_php_json({}) == b"{}\n"


def test_an_empty_list_encodes_as_phps_empty_array_inside_a_response():
    assert encode_php_json({"racers": []}) == b'{\n    "racers": []\n}\n'


def test_an_empty_dict_encodes_as_an_empty_object_inside_a_response():
    assert encode_php_json({"racers": {}}) == b'{\n    "racers": {}\n}\n'


def test_an_infinite_float_from_a_handler_is_refused():
    with pytest.raises(ValueError):
        encode_php_json(float("inf"))
