"""PHP's request variable parsing, as `$_POST` and `$_GET` see a form (#17).

Clients send PHP form arrays (`message[recipient]=AA`, `list[]=1`), and the
handlers and the `action` echo depend on the shape PHP builds from them. This
follows `php_register_variable_ex()` in PHP's `main/php_variables.c`.
"""

import re

from django.http import QueryDict

_INTEGER_KEY = re.compile(r"-?(?:0|[1-9][0-9]*)")


def _php_key(index: str) -> str | int:
    """A canonical decimal integer index is an int key in PHP, as `"5"` is."""
    if _INTEGER_KEY.fullmatch(index) and index != "-0":
        number = int(index)
        if -(2**63) <= number < 2**63:
            return number
    return index


def _next_index(array: dict) -> int:
    ints = [key for key in array if isinstance(key, int)]
    return max(ints) + 1 if ints else 0


def _register(target: dict, name: str, value: str) -> None:
    name = name.lstrip(" ")
    bracket = name.find("[")
    base = (name if bracket < 0 else name[:bracket]).replace(" ", "_")
    base = base.replace(".", "_")
    if not base:
        return
    if bracket < 0:
        target[base] = value
        return

    indexes = []
    rest = name[bracket:]
    while rest.startswith("["):
        close = rest.find("]")
        if close < 0:
            if not indexes:
                # An unclosed first bracket is part of the name, as "_".
                target[f"{base}_{rest[1:]}"] = value
                return
            break
        indexes.append(rest[1:close])
        rest = rest[close + 1 :]

    array, key = target, base
    for index in indexes:
        child = array.get(key)
        if not isinstance(child, dict):
            child = array[key] = {}
        array = child
        key = _next_index(array) if index == "" else _php_key(index)
    array[key] = value


def _as_php_value(value):
    """A nested array whose keys are 0 to n-1 in order is a list, as
    `json_encode()` and PHP's `array_is_list()` treat it."""
    if not isinstance(value, dict):
        return value
    items = {key: _as_php_value(item) for key, item in value.items()}
    if list(items) == list(range(len(items))):
        return list(items.values())
    return items


def parse_php_form(querydict: QueryDict) -> dict:
    """The `$_POST` or `$_GET` array PHP builds from the same fields.

    `a[b]=1` gives `{"a": {"b": "1"}}`, `a[]=1` a list, `a.b` the name `a_b`,
    a bare key `""`, and a repeated key its last value.
    """
    parsed: dict = {}
    for name, values in querydict.lists():
        for value in values:
            _register(parsed, name, value)
    return {key: _as_php_value(value) for key, value in parsed.items()}
