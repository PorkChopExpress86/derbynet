from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ui.docs import build


class Command(BaseCommand):
    help = (
        "Render the guides in docs/ to the pages under /docs/ (#61), into "
        "DERBYNET_DOCS_ROOT (default server/var/docs). The Docker image runs it "
        "at build time."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", type=Path, help="Write here instead of DERBYNET_DOCS_ROOT."
        )

    def handle(self, *args, out=None, **options):
        out = out or settings.DOCS_ROOT
        try:
            written = build.build(out)
        except build.BuildError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(f"wrote {len(written)} files into {out}")
