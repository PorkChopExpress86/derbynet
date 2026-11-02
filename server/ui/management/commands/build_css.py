from django.core.management.base import BaseCommand, CommandError

from ui import css_build


class Command(BaseCommand):
    help = (
        "Build ui/static/ui/app.css with the pinned Tailwind standalone CLI. "
        "The build itself runs offline and needs no Node."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fetch",
            action="store_true",
            help="Download the pinned CLI into the cache first, if it is not there.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 1 if the committed CSS differs from a fresh build.",
        )

    def handle(self, *args, fetch=False, check=False, **options):
        try:
            if fetch:
                path = css_build.fetch()
                self.stdout.write(f"Tailwind CLI {css_build.TAILWIND_VERSION}: {path}")
            if check:
                if not css_build.check():
                    raise CommandError(css_build.OUT_OF_DATE.format(css_build.OUTPUT))
                self.stdout.write(f"{css_build.OUTPUT} is up to date.")
            else:
                self.stdout.write(f"wrote {css_build.build()}")
        except css_build.BuildError as e:
            raise CommandError(str(e)) from e
