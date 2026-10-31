from django.core.management.base import BaseCommand

from core.models import Event
from core.services import event_dir, new_event
from core.setup_event import SUBFOLDERS


class Command(BaseCommand):
    help = (
        "Create one empty active event, so the shell suites can reset it before"
        " setup.nodata exists (#63), with the folders setup.nodata makes, so the"
        " photo suites can upload (#35). Does nothing if an event exists."
    )

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Test Event")

    def handle(self, *args, name, **options):
        if Event.objects.exists():
            self.stdout.write("An event already exists; nothing created.")
            return
        event = new_event(name)
        for sub in SUBFOLDERS:
            (event_dir(event.slug) / sub).mkdir(parents=True, exist_ok=True)
        self.stdout.write(f"Created the active event {event.name!r} ({event.slug}).")
