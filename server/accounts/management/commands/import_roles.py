import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.config_roles import ConfigRolesError, read
from accounts.models import Role


class Command(BaseCommand):
    help = (
        "Replace every role with the roles of a PHP config-roles.inc, "
        "hashing each password."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the roles and bit masks without writing them.",
        )

    def handle(self, path, dry_run, **options):
        try:
            configs, post_setup_role = read(path)
        except (OSError, ConfigRolesError, subprocess.CalledProcessError) as error:
            raise CommandError(f"cannot read {path}: {error}") from error
        names = [config.name for config in configs]
        if post_setup_role is not None and post_setup_role not in names:
            raise CommandError(f"$post_setup_role names no role: {post_setup_role!r}")

        for config in configs:
            flags = [
                "password" if config.password else "no password",
                "interactive" if config.interactive else "not interactive",
            ]
            if config.name == post_setup_role:
                flags.append("post-setup")
            self.stdout.write(
                f"{config.name!r:24} {config.permissions:>6}  {', '.join(flags)}"
            )
        if dry_run:
            self.stdout.write("Dry run: no role was written.")
            return

        roles = []
        for position, config in enumerate(configs):
            role = Role(
                name=config.name,
                permissions=config.permissions,
                interactive=config.interactive,
                position=position,
                post_setup=config.name == post_setup_role,
            )
            role.set_password(config.password)
            roles.append(role)
        with transaction.atomic():
            Role.objects.all().delete()
            Role.objects.bulk_create(roles)
        self.stdout.write(f"Imported {len(roles)} roles from {path}.")
