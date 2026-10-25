"""Lane helpers from `website/inc/schedule_utils.inc` and the `reverse-lanes`
mirror in `website/ajax/action.timer-message.inc`."""


def count_one_bits(n: int) -> int:
    return n.bit_count()


def spread_across_unused_lanes[T](
    chart: list[list[T]], mask: int
) -> list[list[T | None]]:
    """Widen each heat to physical lanes, with None in each masked lane.

    Bit 0 of `mask` is lane 1. As in PHP, masked lanes after a heat's last
    car add nothing, so a heat is only as wide as its last used lane.
    """
    if mask == 0:
        return chart
    wide = []
    for narrow_heat in chart:
        p = 0
        wide_heat: list[T | None] = []
        for racer in narrow_heat:
            while mask & (1 << p):
                wide_heat.append(None)
                p += 1
            wide_heat.append(racer)
            p += 1
        wide.append(wide_heat)
    return wide


def mirror_lanes[T](values: list[T], nlanes: int) -> list[T]:
    """Lane `i` takes the value of lane `nlanes - 1 - i`, for `reverse-lanes`.

    Values past `nlanes` keep their places, as in PHP's copy of the array.
    """
    return [values[nlanes - 1 - i] for i in range(nlanes)] + values[nlanes:]
