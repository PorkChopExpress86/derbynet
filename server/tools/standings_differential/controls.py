import functools
import operator
from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType
from unittest import mock

from racing.engine.standings import finalists, oracle, scoring, speed_awards, tracker


def _wrap(target, name, after):
    original = getattr(target, name)

    def wrapper(*args, **kwargs):
        return after(original(*args, **kwargs), *args)

    return mock.patch.object(target, name, wrapper)


def _strict_add(self, score, racer_id):
    self.count += 1
    if score < self._last_score:
        place = len(self.rungs) - 1
        self.rungs[place].append(racer_id)
    else:
        while len(self.rungs) < self.count - 1:
            self.rungs.append([])
        self.rungs.append([racer_id])
        place = self.count - 1
    self._last_score = score
    return place


def _count_one_more(result, standings, *_args):
    counts = dict(standings.catalog_counts)
    for key in list(counts)[:1]:
        counts[key] += 1
    standings.catalog_counts = MappingProxyType(counts)
    return result


def _drop_last_finalist(cut, *args):
    for i in reversed(range(len(cut.buckets))):
        bucket = cut.buckets[i]
        if bucket.finalists:
            shorter = replace(bucket, finalists=bucket.finalists[:-1])
            return replace(
                cut, buckets=(*cut.buckets[:i], shorter, *cut.buckets[i + 1 :])
            )
    return cut


def _drop_tie_awards(current, key, *args):
    place = key.split("-")[1] if key.startswith("speed-") else ""
    return None if place[-1:].isalpha() else current


def _left_to_right(values):
    """Plain float addition. Python's `sum` compensates, so it would not do."""
    return functools.reduce(operator.add, values, 0.0)


CONTROLS = {
    "scores summed left to right": (
        "rows",
        lambda: mock.patch.object(scoring.math, "fsum", _left_to_right),
    ),
    "name order folds accented letters": (
        "rows",
        lambda: mock.patch.object(oracle, "ascii_fold", str.lower),
    ),
    "a tie needs a strictly lower score": (
        "ladders",
        lambda: mock.patch.object(tracker.StandingsTracker, "add", _strict_add),
    ),
    "excluded racers take places": (
        "rows",
        lambda: mock.patch.object(oracle.Standings, "why_not", lambda *a: None),
    ),
    "a catalog count is one more": (
        "catalog_counts",
        lambda: _wrap(oracle.Standings, "_place_rows", _count_one_more),
    ),
    "the catalog loses qualifier standings": (
        "catalog",
        lambda: _wrap(
            oracle.Standings,
            "catalog",
            lambda c, s: tuple(e for e in c if e.presentation != "qual"),
        ),
    ),
    "an aggregate has no class key": (
        "class_keys",
        lambda: _wrap(
            oracle.Standings,
            "class_key",
            lambda k, s, g: None if s.groups[g].constituents else k,
        ),
    ),
    "a subgroup has no rank key": (
        "rank_keys",
        lambda: _wrap(oracle.Standings, "rank_key", lambda k, *a: None),
    ),
    "aggregates in reverse order": (
        "topological_order",
        lambda: _wrap(speed_awards, "_aggregate_order", lambda o, s: o[::-1]),
    ),
    "award names lose the tie mark": (
        "awards",
        lambda: mock.patch.object(
            speed_awards, "nth_fastest", lambda n, label: f"{n} {label}"
        ),
    ),
    "reserved_overall is one more": (
        "reserved_overall",
        lambda: _wrap(
            speed_awards,
            "speed_awards",
            lambda a, *x: replace(a, reserved_overall=a.reserved_overall + 1),
        ),
    ),
    "a cut loses its last finalist": (
        "finalists",
        lambda: _wrap(finalists, "from_keys", _drop_last_finalist),
    ),
    "a tied award key names no award": (
        "current",
        lambda: _wrap(speed_awards, "current_speed_award", _drop_tie_awards),
    ),
}


@contextmanager
def fault(name: str):
    with CONTROLS[name][1]():
        yield
