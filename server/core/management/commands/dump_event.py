import json

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from core.models import Event, event_scoped_models


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
