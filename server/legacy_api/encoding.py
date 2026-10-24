"""PHP's `json_encode($v, JSON_PRETTY_PRINT | JSON_NUMERIC_CHECK)`, byte for byte.

`website/action.php` encodes every JSON response this way, and the clients
depend on its quirks (#17): a numeric-looking string becomes a number, a slash
is written `\\/`, and anything past ASCII is a `\\uXXXX` escape. The expected
outputs in `tests/data/php_encoding.json` come from PHP itself.
"""

import math
import re
from decimal import Decimal

INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1

# PHP 8's is_numeric(): optional whitespace on both sides, a sign, digits with
# an optional decimal point, and an optional exponent. Hex is not numeric.
_NUMERIC = re.compile(
    r"[ \t\n\r\v\f]*([+-]?(?:[0-9]+(\.[0-9]*)?|(\.)[0-9]+)([eE][+-]?[0-9]+)?)"
    r"[ \t\n\r\v\f]*"
)

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "/": "\\/",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def is_php_numeric(s: str) -> bool:
    return _NUMERIC.fullmatch(s) is not None


def php_number(s: str) -> int | float | None:
    """The number PHP reads from a numeric string, or None if it is not one.

    An integral literal that fits in 64 bits is an int. Anything else numeric
    is a float, which may be infinite.
    """
    match = _NUMERIC.fullmatch(s)
    if match is None:
        return None
    literal, fraction, leading_point, exponent = match.groups()
    if fraction is None and leading_point is None and exponent is None:
        value = int(literal)
        if INT64_MIN <= value <= INT64_MAX:
            return value
    return float(literal)


def php_float(d: float, exp_char: str = "e") -> str:
    """PHP's `zend_gcvt()` with `serialize_precision = -1`.

    The digits are the shortest that round-trip, as Python's `repr()` gives,
    but PHP uses an exponent only below 1e-4 or from 1e17 up, and writes it
    as `1.0e+25`, not `1e+25`.
    """
    sign = "-" if math.copysign(1.0, d) < 0 else ""
    if d == 0:
        return sign + "0"
    _, digit_tuple, exponent = Decimal(repr(abs(d))).as_tuple()
    digits = "".join(map(str, digit_tuple))
    point = len(digits) + exponent
    digits = digits.rstrip("0")
    if point < -3 or point > 17:
        power = point - 1
        mantissa = f"{digits[0]}.{digits[1:] or '0'}"
        return f"{sign}{mantissa}{exp_char}{'-' if power < 0 else '+'}{abs(power)}"
    if point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    whole = digits[:point].ljust(point, "0")
    fraction = digits[point:]
    return f"{sign}{whole}.{fraction}" if fraction else sign + whole


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif code < 0x20 or 0x7F < code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        elif code > 0xFFFF:
            code -= 0x10000
            out.append(
                f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"
            )
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _scalar_string(s: str) -> str:
    number = php_number(s)
    if isinstance(number, int):
        return str(number)
    if isinstance(number, float) and math.isfinite(number):
        return php_float(number)
    return _string(s)


def _encode(value, depth: int, out: list[str]) -> None:
    match value:
        case None:
            out.append("null")
        case bool():
            out.append("true" if value else "false")
        case int():
            out.append(str(value))
        case float():
            if not math.isfinite(value):
                raise ValueError(f"PHP cannot encode {value} in JSON")
            out.append(php_float(value))
        case str():
            out.append(_scalar_string(value))
        case list() | tuple():
            _container("[", "]", [(None, item) for item in value], depth, out)
        case dict():
            _container("{", "}", list(value.items()), depth, out)
        case _:
            raise TypeError(f"cannot encode {type(value).__name__} as PHP JSON")


def _container(open_, close, items, depth: int, out: list[str]) -> None:
    if not items:
        out.append(open_ + close)
        return
    inner = "    " * (depth + 1)
    out.append(open_ + "\n")
    for i, (key, item) in enumerate(items):
        out.append(inner)
        if key is not None:
            out.append(_string(str(key)) + ": ")
        _encode(item, depth + 1, out)
        out.append(",\n" if i < len(items) - 1 else "\n")
    out.append("    " * depth + close)


def encode_php_json(value) -> bytes:
    """The response body PHP writes for `value`, trailing newline included."""
    out: list[str] = []
    _encode(value, 0, out)
    out.append("\n")
    return "".join(out).encode("ascii")
