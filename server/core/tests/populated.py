from awards.models import AwardPartition
from awards.tests.factories import AwardFactory, BallotAwardFactory, VoteFactory
from core.models import RaceSettings, RaceState
from core.tests.factories import ActionLogFactory, EventFactory
from kiosks.models import Scene
from kiosks.tests.factories import (
    KioskFactory,
    ReplayCommandFactory,
    SignalMessageFactory,
)
from racing.models import (
    AuditEvent,
    EmptiedHeat,
    FinalRequest,
    LaneClosure,
    TimerStatus,
)
from racing.tests.factories import (
    AuditLaneResultFactory,
    HeatLaneFactory,
    PlaylistEntryFactory,
    RosterEntryFactory,
    TimerSettingFactory,
)
from roster.tests.factories import (
    GroupConstituentFactory,
    GroupConstituentSubgroupFactory,
)
from transfer.models import LegacyId


def populated_event():
    """An event with rows in every event-scoped model and every optional
    reference between them set, all inside the event."""
    event = EventFactory()
    entry = RosterEntryFactory(round__group__event=event)
    round_, racer = entry.round, entry.racer
    scene = Scene.objects.filter(event=event).first()
    HeatLaneFactory(round=round_, racer=racer, lane=1)
    EmptiedHeat.objects.create(event=event, round=round_, heat=2, master_heat=2)
    FinalRequest.objects.create(event=event, racer=racer, group=round_.group)
    LaneClosure.objects.create(event=event, round=round_, mask=0b100)
    link = GroupConstituentFactory(aggregate__event=event)
    GroupConstituentSubgroupFactory(aggregate=link.aggregate, subgroup=racer.subgroup)
    step = PlaylistEntryFactory(group=round_.group, scene_at_finish=scene)
    TimerSettingFactory(event=event)
    lane_result = AuditLaneResultFactory(
        audit_event__event=event,
        audit_event__racer=racer,
        audit_event__group=round_.group,
        audit_event__subgroup=racer.subgroup,
        audit_event__round=round_,
    )
    AuditEvent.objects.filter(pk=lane_result.audit_event_id).update(
        result_of=lane_result.audit_event_id
    )
    award = AwardFactory(
        event=event, group=racer.group, subgroup=racer.subgroup, racer=racer
    )
    AwardPartition.objects.create(event=event, award=award, partition=racer.partition)
    VoteFactory(voter__event=event)
    BallotAwardFactory(award__event=event)
    KioskFactory(event=event)
    SignalMessageFactory(event=event)
    ReplayCommandFactory(event=event)
    ActionLogFactory(event=event)
    LegacyId.objects.create(
        event=event, model="roster.racer", legacy_id=1, new_id=racer.id
    )
    RaceSettings.objects.filter(event=event).update(
        full_field_rule="group", full_field_group=link.aggregate, racing_scene=scene
    )
    RaceState.objects.filter(event=event).update(
        current_group=round_.group,
        current_round=round_,
        current_scene=scene,
        playlist_scene=scene,
        waiting_entry=step,
        ended_round=round_,
    )
    TimerStatus.objects.filter(event=event).update(
        staged_round=round_, test_saved_round=round_
    )
    return event
