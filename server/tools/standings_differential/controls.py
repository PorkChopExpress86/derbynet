"""Seeded faults that prove the comparison can fail. Each control breaks the
Django engine in one way, and the comparison must report a difference in
the category named for it. `--controls` runs them on the events of the run.
"""

import functools
import operator
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from racing.engine.standings import finalists, oracle, scoring, speed_awards, tracker
from racing.engine.standings.eligibility import AwardSettings


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


def _count_one_more(result, standings):
    for key in list(standings.catalog_counts)[:1]:
        standings.catalog_counts[key] += 1
    return result


def _drop_last_finalist(cut, *args):
    return replace(cut, finalists=cut.finalists[:-1]) if cut.finalists else cut


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
        lambda: mock.patch.object(AwardSettings, "why_not", lambda self, **kw: None),
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
            lambda c, s: [e for e in c if e.presentation != "qual"],
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
