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
            "--time-zone",
            help="The IANA time zone the legacy server kept local times in."
            " Default: this server's TIME_ZONE.",
        )
        parser.add_argument(
            "--report", type=Path, help="Write the full report as JSON."
        )
        parser.add_argument(
            "--force-report",
            action="store_true",
            help="Overwrite the --report file when it exists.",
        )

    def handle(self, *args, source, report, force_report, **options):
        if report is not None:
            _check_report_path(report, source, force_report)
        try:
            result = import_derbynet(
                source,
                event_name=options["event"],
                photos_dir=options["photos_dir"],
                car_photos_dir=options["car_photos_dir"],
                replace=options["replace"],
                accept_violations=options["accept_violations"],
                time_zone=options["time_zone"],
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


def _check_report_path(report, source, force):
    """Refuse a report path that is the source or one of its SQLite side files,
    or that exists without --force-report."""
    resolved = report.resolve()
    origin = source.resolve()
    side_files = {
        origin.with_name(origin.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    }
    same = resolved == origin or resolved in side_files
    if not same and report.exists() and source.exists():
        same = report.samefile(source)
    if same:
        raise CommandError(
            f"--report {report} is the source database or one of its side files"
        )
    if report.exists() and not force:
        raise CommandError(
            f"--report {report} exists; pass --force-report to overwrite it"
        )
