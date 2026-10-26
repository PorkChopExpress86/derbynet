"""`StandingsTracker` from `website/inc/standings.inc`: one ladder, built
from scores fed in best first.

A ladder is a list of rungs, rung i holding the racers in place i + 1. A
tie of k racers shares one rung and leaves k - 1 empty rungs after it, so
`[[11], [1], [21, 41], [], [31]]` is a tie for third and a fifth place.
"""

Ladder = list[list[int]]


class StandingsTracker:
    def __init__(self):
        self.rungs: Ladder = []
        self.count = 0
        self._last_score = -1.0

    def add(self, score: float, racer_id: int) -> int:
        """Add the next racer and return its zero-based rung. A score less
        than or equal to the last one joins the last rung, exactly as PHP
        compares, so only exact equality ties."""
        self.count += 1
        if score <= self._last_score:
            place = len(self.rungs) - 1
            self.rungs[place].append(racer_id)
        else:
            while len(self.rungs) < self.count - 1:
                self.rungs.append([])
            self.rungs.append([racer_id])
            place = self.count - 1
        self._last_score = score
        return place
