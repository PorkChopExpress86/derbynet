"""Place names from `website/inc/ordinals.inc`, ported unchanged (Q10).

Award names print on certificates, so PHP's oddities stay: 111 is `111st`,
and 0 has no ordinal.
"""

_FIRST_THREE = {1: "1st", 2: "2nd", 3: "3rd"}


def ordinal(n: int | str) -> str | None:
    """`1st`, `2nd`, ... for a place, or `T1st`, ... for a tie mark `"T1"`.

    None for a place below 1, where PHP reads an undefined array key and
    gets null. The tie mark `"T0"` is `"T"`.
    """
    if isinstance(n, str):
        if n.startswith("T"):
            return "T" + (ordinal(n[1:]) or "")
        n = int(n)
    if n > 20 and n % 10 != 0:
        return f"{n // 10}{ordinal(n % 10)}"
    if n < 4:
        return _FIRST_THREE.get(n)
    return f"{n}th"


def nth_fastest(n: int, label: str) -> str:
    """`Fastest in <label>` for place 1, `<ordinal> Fastest in <label>` for
    any other place."""
    prefix = "" if n == 1 else f"{ordinal(n) or ''} "
    return f"{prefix}Fastest in {label}"


def nth_fastest_in_pack(n: int, supergroup_label: str) -> str:
    return nth_fastest(n, supergroup_label)
