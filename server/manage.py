#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "derbynet.settings")
    from django.core.management import execute_from_command_line
    from django.db import OperationalError, connection

    try:
        execute_from_command_line(sys.argv)
    except OperationalError as error:
        # A command run with `docker compose exec` while the compose postgres
        # service is down: one line saying what to start, not Django's
        # traceback (#308). An error after the connection opened keeps its
        # traceback, as does any other database.
        from django.conf import settings

        host = getattr(settings, "COMPOSE_POSTGRES_HOST", None)
        if host is None or connection.connection is not None:
            raise
        reason = str(error).strip().splitlines()[0]
        sys.exit(
            f"derbynet: could not connect to the Postgres at {host}: {reason}\n"
            "Start it with 'docker compose --profile postgres up -d', "
            "or check DERBYNET_POSTGRES_HOST."
        )


if __name__ == "__main__":
    main()
