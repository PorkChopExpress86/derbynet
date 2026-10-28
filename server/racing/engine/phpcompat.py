"""PHP's numeric and comparison rules, for engine code that must match PHP
byte for byte (#20, "Numeric and comparison rules")."""

import math
import re


def php_round(x: float) -> int:
    """PHP `round($x)`: half away from zero, where Python's `round()` goes
    to even. `php_round(2.5)` is 3 and `php_round(-2.5)` is -3."""
    whole = math.floor(abs(x))
    # abs(x) - whole is exact for any float, so a true half is never missed
    # and 0.49999999999999994 does not round up, as `floor(x + 0.5)` would.
    if abs(x) - whole >= 0.5:
        whole += 1
    return int(math.copysign(whole, x))


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def ascii_fold(name: str) -> str:
    """A sort key that compares as SQLite's `COLLATE NOCASE`, which folds
    ASCII letters only. `str.lower()` would also fold `É`."""
    return name.translate(_ASCII_LOWER)


# PHP 8's numeric strings: optional leading and trailing whitespace around a
# decimal number. ASCII digits only, where Python's `float()` takes any.
_NUMBER = re.compile(
    r"[ \t\n\r\v\f]*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)"
)
_WHITESPACE = re.compile(r"[ \t\n\r\v\f]*\Z")
_INTEGER_KEY = re.compile(r"(0|-?[1-9][0-9]*)\Z")


def _number(text: str) -> int | float:
    return float(text) if re.search(r"[.eE]", text) else int(text)


def php_numeric(text: str) -> int | float | None:
    """The value of a PHP numeric string, or None for any other text."""
    match = _NUMBER.match(text)
    if match and _WHITESPACE.match(text, match.end()):
        return _number(match[1])
    return None


PHP_INT_MAX = 2**63 - 1
PHP_INT_MIN = -(2**63)


def php_int(number: int | float) -> int:
    """The int PHP 8 takes from a string's number, in `intval()` and in
    integer arithmetic such as `%`: cut toward zero, infinity as 0, and a
    number past the int range as `PHP_INT_MAX` or `PHP_INT_MIN`. So
    `intval("1e999")` is 0 and `intval("1e19")` is `PHP_INT_MAX`."""
    if isinstance(number, float):
        if not math.isfinite(number):
            return 0
        if not -(2.0**63) <= number < 2.0**63:
            return PHP_INT_MAX if number > 0 else PHP_INT_MIN
        return int(number)
    return min(max(number, PHP_INT_MIN), PHP_INT_MAX)


def php_empty(text: str) -> bool:
    """`empty($text)` for a posted string: true for `""` and `"0"` only, so
    `"0.0"` is not empty."""
    return text in ("", "0")


def php_truthy(value) -> bool:
    """A posted value as PHP's `if ($value)` reads it: a string is false
    only when `php_empty` holds."""
    if isinstance(value, str):
        return not php_empty(value)
    return bool(value)


def php_intval(text: str) -> int:
    """`intval($text)`: the leading number as `php_int` converts it, or 0."""
    match = _NUMBER.match(text)
    return php_int(_number(match[1])) if match else 0


def php_equals(value: int | None, text: str) -> bool:
    """PHP 8's `$value == $text` for an int or null and a string: numbers
    compare as numbers, an int and other text as strings, and null equals
    only the empty string."""
    if value is None:
        return text == ""
    number = php_numeric(text)
    return number == value if number is not None else str(value) == text


def php_compare(value: int | str, other: int) -> int:
    """The sign of PHP 8's `$value <=> $other` for an int or string and an
    int."""
    if isinstance(value, str):
        number = php_numeric(value)
        if number is None:
            right = str(other)
            return (value > right) - (value < right)
        value = number
    return (value > other) - (value < other)


def php_arithmetic_operand(text: str) -> int | float:
    """The number PHP 8 takes from a string in arithmetic: the leading
    number of a numeric or leading-numeric string. Other text raises
    `TypeError`, as PHP does."""
    match = _NUMBER.match(text)
    if not match:
        raise TypeError(f"Unsupported operand types: string, {text!r}")
    return _number(match[1])


def php_array_key(text: str) -> int | str:
    """The key PHP stores for a string array index: an int for a canonical
    decimal integer, else the string."""
    return int(text) if _INTEGER_KEY.match(text) else text
