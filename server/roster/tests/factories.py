import factory

from core.tests.factories import EventFactory
from roster.models import (
    Group,
    GroupConstituent,
    GroupConstituentSubgroup,
    Partition,
    Racer,
    Subgroup,
)


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group

    event = factory.SubFactory(EventFactory)
    name = factory.Sequence(lambda n: f"Group {n}")
    sort_order = factory.Sequence(lambda n: n + 1)


class GroupConstituentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GroupConstituent

    aggregate = factory.SubFactory(GroupFactory)
    constituent = factory.SubFactory(
        GroupFactory, event=factory.SelfAttribute("..aggregate.event")
    )
    event = factory.SelfAttribute("aggregate.event")


class SubgroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Subgroup

    group = factory.SubFactory(GroupFactory)
    event = factory.SelfAttribute("group.event")
    name = factory.Sequence(lambda n: f"Subgroup {n}")
    sort_order = factory.Sequence(lambda n: n + 1)


class PartitionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Partition

    subgroup = factory.SubFactory(SubgroupFactory)
    event = factory.SelfAttribute("subgroup.event")
    name = factory.Sequence(lambda n: f"Partition {n}")
    sort_order = factory.Sequence(lambda n: n + 1)


class RacerFactory(factory.django.DjangoModelFactory):
    """A racer whose subgroup and partition belong to its group."""

    class Meta:
        model = Racer

    group = factory.SubFactory(GroupFactory)
    event = factory.SelfAttribute("group.event")
    subgroup = factory.SubFactory(
        SubgroupFactory,
        group=factory.SelfAttribute("..group"),
        event=factory.SelfAttribute("..event"),
    )
    partition = factory.SubFactory(
        PartitionFactory,
        subgroup=factory.SelfAttribute("..subgroup"),
        event=factory.SelfAttribute("..event"),
    )
    car_number = factory.Sequence(lambda n: 100 + n)
    first_name = factory.Sequence(lambda n: f"First{n}")
    last_name = factory.Sequence(lambda n: f"Last{n}")


class GroupConstituentSubgroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GroupConstituentSubgroup

    aggregate = factory.SubFactory(GroupFactory)
    subgroup = factory.SubFactory(
        SubgroupFactory, group__event=factory.SelfAttribute("...aggregate.event")
    )
    event = factory.SelfAttribute("aggregate.event")
