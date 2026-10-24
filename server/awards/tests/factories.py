import factory

from awards.models import Award, AwardType, BallotAward, Vote, Voter
from core.tests.factories import EventFactory
from roster.tests.factories import RacerFactory


class AwardFactory(factory.django.DjangoModelFactory):
    """A pack-wide award of the seeded type 3, Other."""

    class Meta:
        model = Award

    event = factory.SubFactory(EventFactory)
    name = factory.Sequence(lambda n: f"Award {n}")
    award_type = factory.LazyFunction(lambda: AwardType.objects.get(name="Other"))


class BallotAwardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BallotAward

    award = factory.SubFactory(AwardFactory)
    event = factory.SelfAttribute("award.event")
    depth = 1


class VoterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Voter

    event = factory.SubFactory(EventFactory)
    identifier = factory.Sequence(lambda n: f"session-{n}")


class VoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Vote

    voter = factory.SubFactory(VoterFactory)
    event = factory.SelfAttribute("voter.event")
    award = factory.SubFactory(AwardFactory, event=factory.SelfAttribute("..event"))
    racer = factory.SubFactory(
        RacerFactory, group__event=factory.SelfAttribute("...event")
    )
