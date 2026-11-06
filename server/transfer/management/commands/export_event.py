from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Event
from transfer.export import export_event, file_name


class Command(BaseCommand):
    help = (
        "Export one event as a zip: a standalone SQLite database and the"
        " event's media folders. import_derbynet reads the zip back (#9, #212)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "slug", help="The event's slug, as the events page lists it."
        )
        parser.add_argument(
            "--output",
            type=Path,
            help="Where to write the zip. Default: <slug>.zip in this folder.",
        )
        parser.add_argument(
            "--force", action="store_true", help="Overwrite an existing --output file."
        )

    def handle(self, *args, slug, output, force, **options):
        event = Event.objects.filter(slug=slug).first()
        if event is None:
            raise CommandError(f"no event has the slug {slug!r}")
        target = output or Path(file_name(event))
        if target.exists() and not force:
            raise CommandError(f"{target} exists; pass --force to overwrite it")
        try:
            export_event(event, target)
        except OSError as error:
            raise CommandError(f"cannot write {target}: {error}") from None
        self.stdout.write(f"Exported {event.name} to {target}")
