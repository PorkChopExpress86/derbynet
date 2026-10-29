"""`database.purge`: clear results, schedules, racers or awards (#36).

Ported from `website/ajax/action.database.purge.inc`. Each mode runs in one
transaction that first takes the `RaceState` lock. A purge that empties the
chart or the results of the running round cancels it through the heat-state
service, never by writing the state.
"""

from django.db import transaction

from awards.models import Award, BallotAward
from core.fake_images import remove_fake_images
from core.models import ActionLog, Event, FullFieldRule, RaceSettings, RaceState
from core.services import lock_race_state
from racing.engine import heat_state as hs
from racing.models import AuditEvent, EmptiedHeat, HeatLane, RosterEntry, Round
from racing.services import heats, schedule
from roster.models import Group, Partition, Racer
from roster.services import groups

MODES = ("results", "schedules", "racers", "awards")
Action = AuditEvent.Action


def _audit(event: Event, action) -> None:
    AuditEvent.objects.create(event=event, action=action)


def _round_of(event: Event, round_id) -> Round | None:
    """PHP binds the posted id as it is, so one that names no round clears
    no row."""
    try:
        return Round.objects.filter(event=event, pk=int(round_id)).first()
    except TypeError, ValueError:
        return None


def purge(event: Event, what: str, round_id=None) -> bool:
    """Run one mode. Returns False for a mode this does not know."""
    match what:
        case "results":
            _results(event, round_id)
        case "schedules":
            _schedules(event, round_id)
        case "racers":
            _racers(event)
        case "awards":
            _awards(event)
        case _:
            return False
    return True


def _results(event: Event, round_id) -> None:
    with transaction.atomic():
        state = lock_race_state(event.id)
        clear = {"finish_time": None, "finish_place": None, "completed": None}
        lanes = HeatLane.objects.filter(event=event)
        if round_id is None:
            _audit(event, Action.PURGE_RESULTS)
            lanes.update(**clear)
            heats.transition(state, hs.Cancel())
            return
        _audit(event, Action.PURGE_RESULTS_ONE_ROUND)
        round = _round_of(event, round_id)
        if round is not None:
            lanes.filter(round=round).update(**clear)
            heats.maybe_cancel_running_round(state, round.id)


def _schedules(event: Event, round_id) -> None:
    _audit(event, Action.PURGE_SCHEDULES)
    if round_id is not None:
        round = _round_of(event, round_id)
        if round is not None:
            schedule.unschedule(round)
        return
    for round in Round.objects.filter(event=event).order_by("id"):
        schedule.unschedule(round)
    heats.cancel_running_round(event)


def _racers(event: Event) -> None:
    settings = RaceSettings.objects.get(event=event)
    folders = (settings.photo_directory, settings.car_photo_directory)
    with transaction.atomic():
        state = lock_race_state(event.id)
        _audit(event, Action.PURGE_RACERS)
        Award.objects.filter(event=event).update(racer=None)
        HeatLane.objects.filter(event=event).delete()
        EmptiedHeat.objects.filter(event=event).delete()
        RosterEntry.objects.filter(event=event).delete()
        Racer.objects.filter(event=event).delete()
        ActionLog.objects.create(event=event, request={"action": "racers.*purge"})
        # Groups, subgroups and partitions stay, except fake racers' ones.
        if state.fake_racers:
            for group in Group.objects.filter(event=event).order_by("-id"):
                if Group.objects.filter(pk=group.pk).exists():
                    groups.delete_group_rows(group)
            Partition.objects.filter(event=event).delete()
            groups.record_partition_structure(event.id)
        RaceState.objects.filter(pk=state.pk).update(fake_racers=False)
        RaceSettings.objects.filter(event=event).update(
            full_field_rule=FullFieldRule.STANDARD, full_field_group=None
        )
        heats.transition(RaceState.objects.get(pk=state.pk), hs.Cancel())
        transaction.on_commit(lambda: [remove_fake_images(f) for f in folders])


def _awards(event: Event) -> None:
    with transaction.atomic():
        lock_race_state(event.id)
        _audit(event, Action.PURGE_AWARDS)
        BallotAward.objects.filter(event=event).delete()
        Award.objects.filter(event=event).delete()
        ActionLog.objects.create(event=event, request={"action": "awards.*purge"})
