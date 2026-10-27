"""Place names from `website/inc/ordinals.inc`, ported unchanged (Q10).

Award names print on certificates, so PHP's oddities stay: 111 is `111st`,
and 0 has no ordinal. `query.award.current` can pass the place text of a
key as it was typed, such as `02`, so a string place follows PHP 8's
comparison and arithmetic rules (`phpcompat`).
"""

import math

from racing.engine.phpcompat import (
    php_arithmetic_operand,
    php_array_key,
    php_compare,
    php_equals,
    php_int,
)

_FIRST_THREE = {1: "1st", 2: "2nd", 3: "3rd"}


def ordinal(n: int | str) -> str | None:
    """`1st`, `2nd`, ... for a place, or `T1st`, ... for a tie mark `"T1"`.

    None for a place below 1, where PHP reads an undefined array key and
    gets null. The tie mark `"T0"` is `"T"`. A string that is not a whole
    number reads no key either, so `ordinal("02")` is None. Raises
    `TypeError` where PHP does, for a string above `"20"` that does not
    start with a number.
    """
    if isinstance(n, str) and n.startswith("T"):
        return "T" + (ordinal(n[1:]) or "")
    if php_compare(n, 20) > 0:
        value = n if isinstance(n, int) else php_arithmetic_operand(n)
        whole = php_int(value)
        rest = abs(whole) % 10 * (1 if whole >= 0 else -1)
        if rest != 0:
            return f"{_float_text(float(math.floor(value / 10)))}{ordinal(rest) or ''}"
    if php_compare(n, 4) < 0:
        return _FIRST_THREE.get(n if isinstance(n, int) else php_array_key(n))
    return f"{n}th"


def _float_text(value: float) -> str:
    """PHP's text for a float at its default `precision` of 14 digits, as
    `.` writes the float `floor()` returns: `25`, or `1.0E+18` where
    Python's `%.14G` writes `1E+18`."""
    mantissa, _, exponent = f"{value:.14G}".partition("E")
    if not exponent:
        return mantissa
    if "." not in mantissa:
        mantissa += ".0"
    return f"{mantissa}E{exponent[0]}{int(exponent[1:])}"


def nth_fastest(n: int | str, label: str) -> str:
    first = n == 1 if isinstance(n, int) else php_equals(1, n)
    prefix = "" if first else f"{ordinal(n) or ''} "
    return f"{prefix}Fastest in {label}"
