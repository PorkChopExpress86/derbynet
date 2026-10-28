from django.core.management.base import BaseCommand

from core.models import Event
from core.services import new_event


class Command(BaseCommand):
    help = (
        "Create one empty active event, so the shell suites can reset it before"
        " setup.nodata exists (#63). Does nothing if an event exists."
    )

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Test Event")

    def handle(self, *args, name, **options):
        if Event.objects.exists():
            self.stdout.write("An event already exists; nothing created.")
            return
        event = new_event(name)
        self.stdout.write(f"Created the active event {event.name!r} ({event.slug}).")
