import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from transfer.importer import ImportFailed, import_derbynet
from transfer.legacy.source import SourceError


class Command(BaseCommand):
    help = (
        "Import one past DerbyNet event, a SQLite file at schema 1 to 10 or a"
        " snapshot.get file, as a new event. The source is never modified."
    )

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path)
        parser.add_argument(
            "--event", help="The new event's name. Default: the source's folder name."
        )
        parser.add_argument(
            "--photos-dir",
            type=Path,
            help="Where to look for headshots first. Then the source's racers/ folder.",
        )
        parser.add_argument(
            "--car-photos-dir",
            type=Path,
            help="Where to look for car photos first. Then the source's cars/ folder.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace an existing event of the same name.",
        )
        parser.add_argument(
            "--accept-violations",
            action="store_true",
            help="Keep the import even when check_invariants() reports violations.",
        )
        parser.add_argument(
            "--report", type=Path, help="Write the full report as JSON."
        )

    def handle(self, *args, source, report, **options):
        try:
            result = import_derbynet(
                source,
                event_name=options["event"],
                photos_dir=options["photos_dir"],
                car_photos_dir=options["car_photos_dir"],
                replace=options["replace"],
                accept_violations=options["accept_violations"],
            )
        except SourceError as error:
            raise CommandError(str(error)) from None
        except ImportFailed as error:
            if error.report is not None:
                self._write(error.report, report)
            raise CommandError(str(error)) from None
        self._write(result, report)

    def _write(self, result, path):
        if path is not None:
            path.write_text(
                json.dumps(result.as_json(), cls=DjangoJSONEncoder, indent=2) + "\n"
            )
        self.stdout.write(result.summary())
