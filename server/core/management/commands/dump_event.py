import json

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from core.models import Event


def event_scoped_models():
    """Every model with an `event` foreign key, in a stable order."""
    found = []
    for model in apps.get_models():
        field = next((f for f in model._meta.fields if f.name == "event"), None)
        if field is not None and field.related_model is Event:
            found.append(model)
    return sorted(found, key=lambda m: m._meta.label_lower)


def _rows(queryset):
    return [
        {"id": obj["pk"], **obj["fields"]}
        for obj in serializers.serialize("python", queryset.order_by("pk"))
    ]


def dump(event):
    return {
        "event": _rows(Event.objects.filter(pk=event.pk))[0],
        "models": {
            model._meta.label_lower: _rows(model.objects.filter(event=event))
            for model in event_scoped_models()
        },
    }


class Command(BaseCommand):
    help = "Print one event's rows for every event-scoped model as JSON. Read-only."

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, slug, **options):
        try:
            event = Event.objects.get(slug=slug)
        except Event.DoesNotExist:
            raise CommandError(f"No event with slug {slug!r}") from None
        self.stdout.write(json.dumps(dump(event), cls=DjangoJSONEncoder, indent=2))
