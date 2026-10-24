import factory

from core.tests.factories import EventFactory
from kiosks.models import Kiosk, ReplayCommand, Scene, SceneKiosk, SignalMessage


class KioskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Kiosk

    event = factory.SubFactory(EventFactory)
    address = factory.Sequence(lambda n: f"kiosk-{n}")
    name = factory.Sequence(lambda n: f"Kiosk {n}")


class SceneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Scene

    event = factory.SubFactory(EventFactory)
    name = factory.Sequence(lambda n: f"Scene {n}")
    sort_order = factory.Sequence(lambda n: 100 + n)


class SceneKioskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SceneKiosk

    scene = factory.SubFactory(SceneFactory)
    event = factory.SelfAttribute("scene.event")
    kiosk_name = "Main"
    page = "kiosks/welcome.kiosk"


class SignalMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SignalMessage

    event = factory.SubFactory(EventFactory)
    recipient = "replay"
    payload = factory.LazyFunction(lambda: {"cmd": "test"})


class ReplayCommandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ReplayCommand

    event = factory.SubFactory(EventFactory)
    command = "REPLAY 4 2 50"
