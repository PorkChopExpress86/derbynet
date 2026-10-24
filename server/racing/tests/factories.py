import factory

from core.tests.factories import EventFactory
from racing.models import (
    AuditEvent,
    AuditLaneResult,
    HeatLane,
    PlaylistEntry,
    RosterEntry,
    Round,
    TimerSetting,
)
from roster.tests.factories import GroupFactory, RacerFactory


class RoundFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Round

    group = factory.SubFactory(GroupFactory)
    event = factory.SelfAttribute("group.event")
    number = 1


class RosterEntryFactory(factory.django.DjangoModelFactory):
    """A roster entry for a racer of the round's group."""

    class Meta:
        model = RosterEntry

    round = factory.SubFactory(RoundFactory)
    event = factory.SelfAttribute("round.event")
    racer = factory.SubFactory(
        RacerFactory, group=factory.SelfAttribute("..round.group")
    )


class HeatLaneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = HeatLane

    round = factory.SubFactory(RoundFactory)
    event = factory.SelfAttribute("round.event")
    heat = 1
    lane = factory.Sequence(lambda n: n + 1)
    racer = factory.SubFactory(
        RacerFactory, group=factory.SelfAttribute("..round.group")
    )


class PlaylistEntryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PlaylistEntry

    group = factory.SubFactory(GroupFactory)
    event = factory.SelfAttribute("group.event")
    seq = factory.Sequence(lambda n: n + 1)
    round_number = 1


class TimerSettingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TimerSetting

    event = factory.SubFactory(EventFactory)
    kind = TimerSetting.Kind.FLAG
    name = factory.Sequence(lambda n: f"flag{n}")
    value_type = "bool"
    value = "false"


class AuditEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditEvent

    event = factory.SubFactory(EventFactory)
    action = AuditEvent.Action.HEAT_RESULT_FROM_TIMER


class AuditLaneResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditLaneResult

    audit_event = factory.SubFactory(AuditEventFactory)
    event = factory.SelfAttribute("audit_event.event")
    lane = factory.Sequence(lambda n: n + 1)
    finish_time = 3.5
