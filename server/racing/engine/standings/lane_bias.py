"""The lane bias test of `website/inc/lane-bias.inc` and the alpha 0.10
critical values of `website/inc/f-inv.inc`.

A one-way ANOVA over the finish times of each lane. The arithmetic follows
PHP's operation by operation, sums included, so the F statistic matches PHP
to the last bit. Python's `sum()` would not: it compensates float sums since
3.12.
"""

from collections.abc import Sequence
from dataclasses import dataclass

DNF_TIME = 9.9

F_DISTRIBUTION_10 = {
    1: (39.8635, 49.5000, 53.5932, 55.8330, 57.2401, 58.2044, 58.9060),
    2: (8.5263, 9.0000, 9.1618, 9.2434, 9.2926, 9.3255, 9.3491),
    3: (5.5383, 5.4624, 5.3908, 5.3426, 5.3092, 5.2847, 5.2662),
    4: (4.5448, 4.3246, 4.1909, 4.1072, 4.0506, 4.0097, 3.9790),
    5: (4.0604, 3.7797, 3.6195, 3.5202, 3.4530, 3.4045, 3.3679),
    6: (3.7759, 3.4633, 3.2888, 3.1808, 3.1075, 3.0546, 3.0145),
    7: (3.5894, 3.2574, 3.0741, 2.9605, 2.8833, 2.8274, 2.7849),
    8: (3.4579, 3.1131, 2.9238, 2.8064, 2.7264, 2.6683, 2.6241),
    9: (3.3603, 3.0065, 2.8129, 2.6927, 2.6106, 2.5509, 2.5053),
    10: (3.2850, 2.9245, 2.7277, 2.6053, 2.5216, 2.4606, 2.4140),
    11: (3.2252, 2.8595, 2.6602, 2.5362, 2.4512, 2.3891, 2.3416),
    12: (3.1765, 2.8068, 2.6055, 2.4801, 2.3940, 2.3310, 2.2828),
    13: (3.1362, 2.7632, 2.5603, 2.4337, 2.3467, 2.2830, 2.2341),
    14: (3.1022, 2.7265, 2.5222, 2.3947, 2.3069, 2.2426, 2.1931),
    15: (3.0732, 2.6952, 2.4898, 2.3614, 2.2730, 2.2081, 2.1582),
    16: (3.0481, 2.6682, 2.4618, 2.3327, 2.2438, 2.1783, 2.1280),
    17: (3.0262, 2.6446, 2.4374, 2.3077, 2.2183, 2.1524, 2.1017),
    18: (3.0070, 2.6239, 2.4160, 2.2858, 2.1958, 2.1296, 2.0785),
    19: (2.9899, 2.6056, 2.3970, 2.2663, 2.1760, 2.1094, 2.0580),
    20: (2.9747, 2.5893, 2.3801, 2.2489, 2.1582, 2.0913, 2.0397),
    21: (2.9610, 2.5746, 2.3649, 2.2333, 2.1423, 2.0751, 2.0233),
    22: (2.9486, 2.5613, 2.3512, 2.2193, 2.1279, 2.0605, 2.0084),
    23: (2.9374, 2.5493, 2.3387, 2.2065, 2.1149, 2.0472, 1.9949),
    24: (2.9271, 2.5383, 2.3274, 2.1949, 2.1030, 2.0351, 1.9826),
    25: (2.9177, 2.5283, 2.3170, 2.1842, 2.0922, 2.0241, 1.9714),
    26: (2.9091, 2.5191, 2.3075, 2.1745, 2.0822, 2.0139, 1.9610),
    27: (2.9012, 2.5106, 2.2987, 2.1655, 2.0730, 2.0045, 1.9515),
    28: (2.8938, 2.5028, 2.2906, 2.1571, 2.0645, 1.9959, 1.9427),
    29: (2.8870, 2.4955, 2.2831, 2.1494, 2.0566, 1.9878, 1.9345),
    30: (2.8807, 2.4887, 2.2761, 2.1422, 2.0492, 1.9803, 1.9269),
    40: (2.8354, 2.4404, 2.2261, 2.0909, 1.9968, 1.9269, 1.8725),
    60: (2.7911, 2.3933, 2.1774, 2.0410, 1.9457, 1.8747, 1.8194),
    120: (2.7478, 2.3473, 2.1300, 1.9923, 1.8959, 1.8238, 1.7675),
    9999999: (2.7055, 2.3026, 2.0838, 1.9449, 1.8473, 1.7741, 1.7167),
}


def critical_f_value(df1: int, df2: int) -> float | None:
    """The alpha 0.10 critical value of F(df1, df2), from the row of the
    largest tabled df2 not above `df2`.

    None when `df1` is outside 1 to 7 or `df2` is below 1. PHP indexes the
    row with `df1` where the row starts at df1 = 1, so it reads the next
    column and has no value for df1 = 7 (Q9).
    """
    if not 1 <= df1 <= 7:
        return None
    rows = [key for key in F_DISTRIBUTION_10 if key <= df2]
    if not rows:
        return None
    return F_DISTRIBUTION_10[rows[-1]][df1 - 1]


@dataclass(frozen=True)
class LaneStats:
    runs: int
    dnfs: int
    mean: float
    sample_variance: float | None


@dataclass(frozen=True)
class LaneBias:
    """The F test, and the stats of lanes 1 to `nlanes` in order.

    `biased` is PHP 8's `$f > $critical`. When `critical_value` is None, PHP
    compares `$f > null` as booleans, so `biased` is true whenever the F
    statistic is not 0.
    """

    biased: bool
    f_statistic: float
    df1: int
    df2: int
    critical_value: float | None
    lanes: tuple[LaneStats, ...]


def lane_bias_analysis(times_by_lane: Sequence[Sequence[float]]) -> LaneBias | None:
    """The test over `times_by_lane[l - 1]`, the finish times of lane `l` in
    result order, with no zero times. A time of `DNF_TIME` or more is a DNF.

    None where PHP returns an empty array and `history.php` says there
    isn't enough evidence: no finished run, one lane, as many finished runs
    as lanes, or no variation within lanes.

    The sums of squares count every lane once. PHP's second loop,
    `foreach ($lanes as $lane)`, writes through the reference its first
    loop left on the last lane, so PHP counts the second-to-last lane twice
    and leaves out the last lane (Q24). `lanes` holds every lane's own
    stats.
    """
    nlanes = len(times_by_lane)
    lanes = []
    total = 0.0
    nn = 0
    for times in times_by_lane:
        finished = [t for t in times if t < DNF_TIME]
        n = len(finished)
        lane_total = 0.0
        for t in finished:
            lane_total += t
        mean = 0.0 if n == 0 else lane_total / n
        sswithin = 0.0
        for t in finished:
            sswithin += (t - mean) * (t - mean)
        lanes.append((n, len(times) - n, mean, sswithin))
        total += lane_total
        nn += n

    if nn == 0 or nlanes <= 1 or nn == nlanes:
        return None
    grand_mean = total / nn
    ssbetween = 0.0
    sswithin = 0.0
    for n, _dnfs, mean, lane_sswithin in lanes:
        ssbetween += n * (mean - grand_mean) * (mean - grand_mean)
        sswithin += lane_sswithin
    msbetween = ssbetween / (nlanes - 1)
    mswithin = sswithin / (nn - nlanes)
    if mswithin == 0:
        return None

    f_statistic = msbetween / mswithin
    df1 = nlanes - 1
    df2 = nn - nlanes
    critical_value = critical_f_value(df1, df2)
    if critical_value is None:
        biased = f_statistic != 0
    else:
        biased = f_statistic > critical_value
    return LaneBias(
        biased=biased,
        f_statistic=f_statistic,
        df1=df1,
        df2=df2,
        critical_value=critical_value,
        lanes=tuple(
            LaneStats(
                runs=n + dnfs,
                dnfs=dnfs,
                mean=mean,
                sample_variance=None if n == 1 else lane_sswithin / (n - 1),
            )
            for n, dnfs, mean, lane_sswithin in lanes
        ),
    )
