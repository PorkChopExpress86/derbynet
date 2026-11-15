#!/usr/bin/env python
import os
import sys
from pathlib import Path

# The Docker image's marker, which server/docker/Dockerfile writes in its
# static stage and copies into the image.  It sits outside
# /opt/derbynet/server, so a container with the server folder bind-mounted
# over that (checks.sh's reload check) still has it.
IMAGE_MARKER = Path("/opt/derbynet/docker-image")

# The commands that run as root in the image: the three the Dockerfile's
# static stage runs to build the image's files (D103).
# tests/test_manage_py.py holds this list to the Dockerfile's.
ROOT_COMMANDS = frozenset({"collectstatic", "build_timer", "build_docs"})


def refuse_root(argv):
    """In the image, exit before any command but ROOT_COMMANDS runs as root
    (D103).  An import run as root leaves the event's photo folder root's, so
    its photos answer 404 (#327's review).  There is no override."""
    # The subcommand as Django's ManagementUtility.execute reads it.
    command = argv[1] if len(argv) > 1 else "help"
    # The marker first: a host with no os.geteuid, such as Windows, never
    # has it.
    if IMAGE_MARKER.exists() and os.geteuid() == 0 and command not in ROOT_COMMANDS:
        sys.exit(
            f"derbynet: manage.py {command} refuses to run as root. Run it as "
            "the server's user: pass -u derbynet to docker exec or docker "
            "compose exec."
        )


def main():
    refuse_root(sys.argv)
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
