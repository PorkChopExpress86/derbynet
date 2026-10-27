"""Allowed racers in a final, and finalist suggestions (#103, owner
decisions D32, D33, D34).

A racer who passed check-in after the group's round 1 ended, with no round
1 result, can only ask for a place in the group's final, round 2 of the
group (#20, "Race-day flow"). The race coordinator allows or declines it.
An allowed racer is extra: the final holds every qualifier the cut picks,
then each allowed racer, and `racing.services.rounds` enters them at every
build of the final from the `FinalRequest` rows. Allowing never writes a
chart; a final with a chart gets a roster entry with no heats, and the race
coordinator fits the racer in with Add to schedule
(`racing.services.reschedule`).

Once a heat of a final has a result, round 1's results are locked for all
but the race coordinator (#27). `finalist_suggestions` compares the
finalists the standings pick now with the final's qualifiers and changes
nothing. `add_to_final` and `remove_from_final` act on a suggestion.

PHP has no way to ask for a place in a final: its finalists come only
from round 1 results (`calculate_roster_from_keys` in
`website/inc/roster.inc`), and `roster.new` clears a final's roster before
it writes the finalists again. Permission checks belong to the handlers:
only the race coordinator, with `CONTROL_RACE` (D16), allows, declines,
adds or removes. Every writer takes the `RaceState` lock first.
"""

from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from core.services import lock_race_state
from racing.engine.standings import finalists
from racing.models import HAS_RESULT, FinalRequest, HeatLane, PlaylistEntry, Round
from racing.services.standings import standings_snapshot
from roster.models import GroupConstituent, GroupConstituentSubgroup, Racer

FINAL_ROUND = 2
"""The round of a group that allowed racers join: its final (#20)."""


class Failure(StrEnum):
    NOT_CHECKED_IN = "not-checked-in"
    NOT_STARTED = "not-started"
    LATE_RACER = "late-racer"
    RACED_ROUND_1 = "raced-round-1"
    NOT_A_FINAL = "not-a-final"
    WRONG_GROUP = "wrong-group"
    ON_ROSTER = "already-on-roster"
    NOT_ON_ROSTER = "not-on-roster"
    NO_CUT = "no-cut"


class FinalsFailure(Exception):
    def __init__(self, code: Failure, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


# ----- requests -----


def request_final_place(racer: Racer) -> FinalRequest:
    """Check-in asks for a place in the final of the racer's group (#42).

    Only a racer who passed check-in, whose group's round 1 has a chart and
    no unraced heat, and who has no round 1 result. The racer becomes not
    eligible for speed awards (D33). A racer whose round 1 still has
    unraced heats is a late racer of round 1 and is refused with
    `late-racer`. A second request returns the first, in whatever state the
    race coordinator left it.
    """
    with transaction.atomic():
        lock_race_state(racer.event_id)
        racer = Racer.objects.get(pk=racer.pk)
        existing = FinalRequest.objects.filter(racer=racer, group=racer.group).first()
        if existing is not None:
            return existing
        if not racer.passed_inspection:
            raise FinalsFailure(
                Failure.NOT_CHECKED_IN, "The racer has not passed check-in."
            )
        round_1 = Round.objects.filter(group=racer.group, number=1).first()
        lanes = HeatLane.objects.filter(round=round_1)
        if round_1 is None or not lanes.exists():
            raise FinalsFailure(
                Failure.NOT_STARTED,
                "Round 1 is not scheduled yet, so the racer races in it.",
            )
        if lanes.exclude(HAS_RESULT).exists():
            raise FinalsFailure(
                Failure.LATE_RACER,
                "Round 1 still has heats to race. Add the racer to round 1"
                " with Add to schedule instead.",
            )
        if lanes.filter(racer=racer).filter(HAS_RESULT).exists():
            raise FinalsFailure(
                Failure.RACED_ROUND_1,
                "The racer raced round 1, so the standings decide the final.",
            )
        Racer.objects.filter(pk=racer.pk).update(not_eligible_for_speed_awards=True)
        return FinalRequest.objects.create(
            event_id=racer.event_id, racer=racer, group=racer.group
        )


def allow_final_request(request: FinalRequest) -> FinalRequest:
    """The race coordinator allows the request. If the final already has a
    roster, the racer joins it, after the racers there; with a chart the
    racer has no heats until Add to schedule fits the racer in. A final
    made later enters the racer when it is built."""
    with transaction.atomic():
        lock_race_state(request.event_id)
        request = FinalRequest.objects.get(pk=request.pk)
        if request.state != FinalRequest.State.ALLOWED:
            _decide(request, FinalRequest.State.ALLOWED)
        final = _final_of(request.group_id)
        if final is not None and final.roster.exists():
            final.roster.get_or_create(
                event_id=request.event_id, racer_id=request.racer_id
            )
        return request


def decline_final_request(request: FinalRequest) -> FinalRequest:
    """The race coordinator declines the request. A racer allowed before
    leaves the final's roster, and any unraced heats become byes at the next
    reschedule, as a departed racer's do (#24)."""
    with transaction.atomic():
        lock_race_state(request.event_id)
        request = FinalRequest.objects.get(pk=request.pk)
        if request.state != FinalRequest.State.DECLINED:
            _decide(request, FinalRequest.State.DECLINED)
        final = _final_of(request.group_id)
        if final is not None:
            final.roster.filter(racer_id=request.racer_id).delete()
        return request


def _decide(request: FinalRequest, state: FinalRequest.State) -> None:
    request.state = state
    request.decided_at = timezone.now()
    request.save(update_fields=["state", "decided_at"])


def _final_of(group_id: int) -> Round | None:
    return Round.objects.filter(group_id=group_id, number=FINAL_ROUND).first()


def allowed_racers(group_id: int, round_number: int) -> tuple[Racer, ...]:
    """The racers allowed into round `round_number` of the group, in the
    order the race coordinator allowed them. Only a group's final takes
    allowed racers."""
    if round_number != FINAL_ROUND:
        return ()
    return tuple(
        r.racer
        for r in FinalRequest.objects.filter(
            group_id=group_id, state=FinalRequest.State.ALLOWED
        )
        .select_related("racer")
        .order_by("decided_at", "id")
    )


# ----- suggestions -----


@dataclass(frozen=True)
class Suggestions:
    """The racers the standings now put in the final who are not in it,
    best first, and the qualifiers in it whom the standings now leave out,
    in roster order."""

    final: Round
    add: tuple[Racer, ...]
    remove: tuple[Racer, ...]


def _is_final(round: Round) -> bool:
    if round.number > 1:
        return True
    return _is_aggregate(round.group_id)


def _is_aggregate(group_id: int) -> bool:
    return (
        GroupConstituent.objects.filter(aggregate_id=group_id).exists()
        or GroupConstituentSubgroup.objects.filter(aggregate_id=group_id).exists()
    )


def _cut(final: Round, top: int | None, bucketed: bool | None) -> finalists.Cut:
    if top is None:
        step = PlaylistEntry.objects.filter(
            group_id=final.group_id, round_number=final.number
        ).first()
        if step is None:
            raise FinalsFailure(
                Failure.NO_CUT,
                "The final has no playlist step, so its cut is not known.",
            )
        top = step.bucket_limit
        bucketed = step.bucketed if bucketed is None else bucketed
    bucketed = bool(bucketed)
    standings = standings_snapshot(final.event).standings
    if final.number > 1:
        before = Round.objects.get(group_id=final.group_id, number=final.number - 1)
        cut = finalists.from_round(
            standings, before.id, final.group_id, bucketed=bucketed, limit=top
        )
    else:
        cut = finalists.for_aggregate(
            standings, final.group_id, bucketed=bucketed, limit=top
        )
    return cut.unique()


def finalist_suggestions(
    final: Round, top: int | None = None, bucketed: bool | None = None
) -> Suggestions:
    """Compare the finalists the standings pick now with the final's
    qualifiers, its roster less its allowed racers. Changes nothing.

    The cut is the final's playlist step's, unless `top` and `bucketed`
    give it; a final with neither fails with `no-cut`. It reads the round
    before the final, and skips any racer who cannot win the group's speed
    awards (D34). No suggestion names a racer with either award setting.
    """
    if not _is_final(final):
        raise FinalsFailure(Failure.NOT_A_FINAL, "The round is not a final.")
    cut = _cut(final, top, bucketed)
    allowed = {r.pk for r in allowed_racers(final.group_id, final.number)}
    roster = [e.racer for e in final.roster.select_related("racer").order_by("id")]
    qualifiers = {r.pk for r in roster} - allowed
    picked = [f.racer_id for f in cut.finalists]
    by_id = Racer.objects.in_bulk(picked)

    def named(racer: Racer) -> bool:
        return not (racer.exclude or racer.not_eligible_for_speed_awards)

    add = tuple(
        by_id[pk] for pk in picked if pk not in qualifiers and pk not in allowed
    )
    remove = tuple(r for r in roster if r.pk in qualifiers and r.pk not in set(picked))
    return Suggestions(
        final,
        tuple(r for r in add if named(r)),
        tuple(r for r in remove if named(r)),
    )


def _racer_of_final(racer: Racer, final: Round) -> bool:
    if final.number > 1:
        return racer.group_id == final.group_id
    return (
        GroupConstituent.objects.filter(
            aggregate_id=final.group_id, constituent_id=racer.group_id
        ).exists()
        or GroupConstituentSubgroup.objects.filter(
            aggregate_id=final.group_id, subgroup_id=racer.subgroup_id
        ).exists()
    )


def add_to_final(racer: Racer, final: Round) -> None:
    """Put a suggested racer on the final's roster, with no heats until Add
    to schedule fits the racer in (#24). The racer keeps the award settings
    it has, so a racer who qualifies this way stays eligible for speed
    awards (D32)."""
    with transaction.atomic():
        lock_race_state(final.event_id)
        final = Round.objects.get(pk=final.pk)
        if not _is_final(final):
            raise FinalsFailure(Failure.NOT_A_FINAL, "The round is not a final.")
        racer = Racer.objects.get(pk=racer.pk, event_id=final.event_id)
        if not _racer_of_final(racer, final):
            raise FinalsFailure(
                Failure.WRONG_GROUP, "The racer does not race in this final's group."
            )
        if final.roster.filter(racer=racer).exists():
            raise FinalsFailure(Failure.ON_ROSTER, "The racer is already in the final.")
        final.roster.create(event_id=final.event_id, racer=racer)


def remove_from_final(racer: Racer, final: Round) -> None:
    """Take a racer off the final's roster. The racer's unraced heats become
    byes at the next reschedule, as a departed racer's do (#24). An allowed
    racer's request becomes declined, so a rebuild does not enter the racer
    again."""
    with transaction.atomic():
        lock_race_state(final.event_id)
        final = Round.objects.get(pk=final.pk)
        if not _is_final(final):
            raise FinalsFailure(Failure.NOT_A_FINAL, "The round is not a final.")
        entry = final.roster.filter(racer_id=racer.pk).first()
        if entry is None:
            raise FinalsFailure(Failure.NOT_ON_ROSTER, "The racer is not in the final.")
        entry.delete()
        if final.number == FINAL_ROUND:
            for request in FinalRequest.objects.filter(
                racer_id=racer.pk,
                group_id=final.group_id,
                state=FinalRequest.State.ALLOWED,
            ):
                _decide(request, FinalRequest.State.DECLINED)


# ----- the poll -----


def _raced_finals(event) -> list[Round]:
    raced = (
        HeatLane.objects.filter(event=event)
        .filter(HAS_RESULT)
        .values_list("round_id", flat=True)
        .distinct()
    )
    rounds = Round.objects.filter(pk__in=raced).select_related("group")
    return [r for r in rounds.order_by("id") if _is_final(r)]


def poll_finals(event) -> dict:
    """The poll field for the race dashboard (#41): the open requests, and
    for each final with a raced heat and a playlist step, the suggestions
    that change something."""
    requests = FinalRequest.objects.filter(
        event=event, state=FinalRequest.State.REQUESTED
    ).order_by("requested_at", "id")
    suggestions = []
    for final in _raced_finals(event):
        try:
            found = finalist_suggestions(final)
        except FinalsFailure:
            continue
        if found.add or found.remove:
            suggestions.append(
                {
                    "roundid": final.pk,
                    "classid": final.group_id,
                    "add": [r.pk for r in found.add],
                    "remove": [r.pk for r in found.remove],
                }
            )
    return {
        "final-requests": [
            {
                "requestid": r.pk,
                "racerid": r.racer_id,
                "classid": r.group_id,
                "state": str(r.state),
            }
            for r in requests
        ],
        "finalist-suggestions": suggestions,
    }
