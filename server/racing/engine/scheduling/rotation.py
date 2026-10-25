"""Rotation scheduling, ported from `website/inc/schedule_rotation.inc`.

Heat `p` puts car `(p + lane) % ncars` in each lane, and each further run
repeats the whole rotation. PHP also adds `nheats`, `ncars` and `heats` to the
`schedule.generate` reply; this returns the heats only (Q14 in #20).
"""

from .rough import Heat


def make_rotation_schedule(
    nlanes: int, ncars: int, n_times_per_lane: int
) -> list[Heat]:
    return [
        [(pole + lane) % ncars for lane in range(nlanes)]
        for _ in range(n_times_per_lane)
        for pole in range(ncars)
    ]
