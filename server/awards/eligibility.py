"""Who can win a judged award: the checks `award.winner` makes, read once
for a whole event so the judging page can show every award's refusal
before the judge taps (#45, C31).

PHP's checks stay for every judged award (`action.award.winner.inc`): a
racer not eligible for any award (`exclude`) is refused, and the racer must
be in the award's group, through an aggregate's groups, and subgroup. A
racer who is not eligible for speed awards can still win (D33). A racer
whose partition the award's partition list leaves out is refused too (D35,
D38).

`awards.editing.set_winner` and the judging page's services ask the same
`Eligibility`, read under the `RaceState` lock, so the reason the page shows
is the reason the service refuses with.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from awards.models import Award, AwardPartition
from awards.services import _Places
from roster.models import GroupConstituent, GroupConstituentSubgroup, Racer

INELIGIBLE = "ineligible"
WRONG_CLASS = "wrong-class"
WRONG_SUBGROUP = "wrong-subgroup"
NOT_IN_LIST = "not-in-list"


@dataclass(frozen=True)
class Refusal:
    """Why a racer cannot win an award: PHP's code, and its description."""

    code: str
    description: str

    @property
    def legacy_code(self) -> str:
        """The code `award.winner` answers: a partition the list leaves out
        is `ineligible`, as for `exclude`."""
        return INELIGIBLE if self.code == NOT_IN_LIST else self.code


EXCLUDED = Refusal(INELIGIBLE, "Racer not eligible for awards")
OTHER_CLASS = Refusal(WRONG_CLASS, "Award is for a different class")
OTHER_SUBGROUP = Refusal(WRONG_SUBGROUP, "Award is for a different subgroup")
LEFT_OUT = Refusal(NOT_IN_LIST, "Racer not eligible for this award")


class Eligibility:
    """The event's aggregates and award partition lists, read once.

    `refusal(award, racer)` answers in PHP's order: `exclude`, then the
    group, then the subgroup, then the partition list."""

    def __init__(self, event_id):
        self.event_id = event_id
        self.places = _Places.read(event_id)
        self._constituents: dict[int, list[int]] = {}
        for aggregate, constituent in GroupConstituent.objects.filter(
            event_id=event_id
        ).values_list("aggregate_id", "constituent_id"):
            self._constituents.setdefault(aggregate, []).append(constituent)
        self._filters: dict[int, set[int]] = {}
        for aggregate, subgroup in GroupConstituentSubgroup.objects.filter(
            event_id=event_id
        ).values_list("aggregate_id", "subgroup_id"):
            self._filters.setdefault(aggregate, set()).add(subgroup)
        self._stored: dict[int, set[int]] = {}
        for award_id, partition_id in AwardPartition.objects.filter(
            event_id=event_id, award__isnull=False
        ).values_list("award_id", "partition_id"):
            self._stored.setdefault(award_id, set()).add(partition_id)
        self._lists: dict[int, set[int]] = {}

    def _is_aggregate(self, group_id: int) -> bool:
        return group_id in self._constituents or group_id in self._filters

    def _members(self, group_id: int) -> set[int]:
        """An aggregate's groups, through aggregates of aggregates."""
        members, todo = set(), [group_id]
        while todo:
            for constituent in self._constituents.get(todo.pop(), ()):
                if constituent not in members:
                    members.add(constituent)
                    todo.append(constituent)
        return members

    def partitions(self, award: Award) -> set[int]:
        """The partitions the award can go to: its stored list, or every
        partition it draws from."""
        found = self._lists.get(award.pk)
        if found is None:
            every = self.places.draws_from(award.group_id, award.subgroup_id)
            stored = self._stored.get(award.pk)
            found = every & stored if stored else every
            self._lists[award.pk] = found
        return found

    def narrowed(self, award: Award) -> bool:
        """Whether the award keeps a narrowed list, not "all partitions"."""
        return award.pk in self._stored

    def refusal(self, award: Award, racer: Racer) -> Refusal | None:
        group_ok = award.group_id is None or award.group_id == racer.group_id
        subgroup_ok = (
            award.subgroup_id is None or award.subgroup_id == racer.subgroup_id
        )
        if award.group_id is not None and self._is_aggregate(award.group_id):
            group_ok = group_ok or racer.group_id in self._members(award.group_id)
            only = self._filters.get(award.group_id)
            subgroup_ok = not only or racer.subgroup_id in only
        if racer.exclude:
            return EXCLUDED
        if not group_ok:
            return OTHER_CLASS
        if not subgroup_ok:
            return OTHER_SUBGROUP
        if racer.partition_id not in self.partitions(award):
            return LEFT_OUT
        return None

    def winners(self, award: Award, racers: Iterable[Racer]) -> list[Racer]:
        """The racers who can win the award, in the order given."""
        return [r for r in racers if self.refusal(award, r) is None]
