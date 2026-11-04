from django.core.management.base import BaseCommand, CommandError

from ui import timer_build


class Command(BaseCommand):
    help = (
        "Build the browser timer for /timer.php (#50): the Django template from "
        "templates/timer.html, the timer's scripts and CSS copied into "
        "ui/static/timer/, and profiles.js from AllProfiles. The Java step "
        "needs javac, or docker for a JDK container; Ant is never needed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 1 if the committed template differs from templates/timer.html.",
        )
        parser.add_argument(
            "--no-profiles",
            action="store_true",
            help="Skip profiles.js, the one step that needs Java.",
        )

    def handle(self, *args, check=False, no_profiles=False, **options):
        try:
            if check:
                if not timer_build.check_template():
                    raise CommandError(
                        timer_build.OUT_OF_DATE.format(timer_build.TEMPLATE)
                    )
                self.stdout.write(f"{timer_build.TEMPLATE} is up to date.")
                return
            self.stdout.write(f"wrote {timer_build.write_template()}")
            copied = timer_build.copy_sources()
            self.stdout.write(f"copied {len(copied)} files into {timer_build.ASSETS}")
            if not no_profiles:
                self.stdout.write(f"wrote {timer_build.profiles_js()}")
        except timer_build.BuildError as e:
            raise CommandError(str(e)) from e
