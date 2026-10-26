"""PHP's numeric and comparison rules, for engine code that must match PHP
byte for byte (#20, "Numeric and comparison rules")."""

import math


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
