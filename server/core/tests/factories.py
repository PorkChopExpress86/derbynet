import factory

from core.models import ActionLog, Event
from core.services import new_event


class EventFactory(factory.django.DjangoModelFactory):
    """An event made by `new_event()`, with its per-event rows and scenes."""

    class Meta:
        model = Event

    name = factory.Sequence(lambda n: f"Derby {n}")

    @classmethod
    def _create(cls, model_class, name, **kwargs):
        event = new_event(name)
        if kwargs:
            Event.objects.filter(pk=event.pk).update(**kwargs)
            event.refresh_from_db()
        return event


class ActionLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ActionLog

    event = factory.SubFactory(EventFactory)
    request = factory.LazyFunction(lambda: {"action": "heat.select"})
