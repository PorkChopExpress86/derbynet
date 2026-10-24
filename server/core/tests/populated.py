from awards.tests.factories import AwardFactory, BallotAwardFactory, VoteFactory
from core.models import RaceSettings, RaceState
from core.tests.factories import ActionLogFactory, EventFactory
from kiosks.models import Scene
from kiosks.tests.factories import (
    KioskFactory,
    ReplayCommandFactory,
    SignalMessageFactory,
)
from racing.models import AuditEvent, TimerStatus
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


def populated_event():
    """An event with rows in every event-scoped model and every optional
    reference between them set, all inside the event."""
    event = EventFactory()
    entry = RosterEntryFactory(round__group__event=event)
    round_, racer = entry.round, entry.racer
    scene = Scene.objects.filter(event=event).first()
    HeatLaneFactory(round=round_, racer=racer, lane=1)
    link = GroupConstituentFactory(aggregate__event=event)
    GroupConstituentSubgroupFactory(aggregate=link.aggregate, subgroup=racer.subgroup)
    PlaylistEntryFactory(group=round_.group, scene_at_finish=scene)
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
    AwardFactory(event=event, group=racer.group, subgroup=racer.subgroup, racer=racer)
    VoteFactory(voter__event=event)
    BallotAwardFactory(award__event=event)
    KioskFactory(event=event)
    SignalMessageFactory(event=event)
    ReplayCommandFactory(event=event)
    ActionLogFactory(event=event)
    RaceSettings.objects.filter(event=event).update(
        full_field_rule="group", full_field_group=link.aggregate, racing_scene=scene
    )
    RaceState.objects.filter(event=event).update(
        current_group=round_.group,
        current_round=round_,
        current_scene=scene,
        playlist_scene=scene,
    )
    TimerStatus.objects.filter(event=event).update(
        staged_round=round_, test_saved_round=round_
    )
    return event
