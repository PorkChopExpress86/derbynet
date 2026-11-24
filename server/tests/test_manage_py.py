"""manage.py when the compose postgres service does not answer (#308).

A command run with `docker compose exec` while the postgres container is
stopped prints a two-line message naming the host and what to start, not
Django's traceback. The host here, under `.invalid`, never resolves (RFC
6761), as a stopped compose service's name does not.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
PASSWORD = "p@ss:w/rd#%"


def run_manage(tmp_path, engine="postgres", **extra):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    if engine:
        (config / "database-engine").write_text(f"{engine}\n")
    env = {
        name: value
        for name, value in os.environ.items()
        if name != "DATABASE_URL" and not name.startswith(("POSTGRES_", "DERBYNET_"))
    }
    env.update(
        DJANGO_SECRET_KEY="test",
        DERBYNET_DATA_DIR=str(tmp_path / "data"),
        DERBYNET_CONFIG_DIR=str(config),
        DERBYNET_POSTGRES_HOST="postgres.invalid",
        POSTGRES_USER="derbynet",
        POSTGRES_PASSWORD=PASSWORD,
        POSTGRES_DB="derbynet",
        **extra,
    )
    return subprocess.run(
        [sys.executable, "manage.py", "showmigrations", "core"],
        cwd=SERVER,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_stopped_compose_postgres_gets_one_message(tmp_path):
    done = run_manage(tmp_path)

    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    lines = done.stderr.strip().splitlines()
    assert len(lines) == 2, done.stderr
    assert lines[0].startswith(
        "derbynet: could not connect to the Postgres at postgres.invalid: "
    )
    assert lines[1] == (
        "Start it with 'docker compose --profile postgres up -d', "
        "or check DERBYNET_POSTGRES_HOST."
    )
    assert PASSWORD not in done.stderr
    assert not (tmp_path / "data" / "derbynet.sqlite3").exists()


def test_an_explicit_database_url_keeps_the_traceback(tmp_path):
    """Only the compose service gets the message: a DATABASE_URL of the
    user's own fails as Django fails."""
    done = run_manage(
        tmp_path,
        DATABASE_URL="postgres://derbynet:x@postgres.invalid:5432/derbynet",
    )

    assert done.returncode == 1
    assert "Traceback" in done.stderr
    assert "derbynet: could not connect" not in done.stderr


def load_manage():
    import importlib.util

    spec = importlib.util.spec_from_file_location("manage_py", SERVER / "manage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_error_after_the_connection_opened_keeps_the_traceback(
    monkeypatch, settings
):
    """The message is for a connection that never opened; a query that fails
    on an open one is another fault, and Django's traceback shows it."""
    import django.core.management
    import pytest
    from django.db import OperationalError, connections

    wrapper = connections["default"]
    settings.COMPOSE_POSTGRES_HOST = "postgres"

    def fails(argv):
        # libpq's refusal spans lines; the message keeps its first.
        raise OperationalError(
            "connection failed: Connection refused\n"
            "\tIs the server running on that host?"
        )

    monkeypatch.setattr(django.core.management, "execute_from_command_line", fails)
    monkeypatch.setattr(wrapper, "connection", None)
    with pytest.raises(SystemExit) as stopped:
        load_manage().main()
    assert str(stopped.value).splitlines() == [
        "derbynet: could not connect to the Postgres at postgres: "
        "connection failed: Connection refused",
        "Start it with 'docker compose --profile postgres up -d', "
        "or check DERBYNET_POSTGRES_HOST.",
    ]

    monkeypatch.setattr(wrapper, "connection", object())
    with pytest.raises(OperationalError):
        load_manage().main()


# ----- manage.py refuses root in the Docker image (D103, #345) ---------------

DOCKERFILE = SERVER / "docker" / "Dockerfile"
# A manage.py command the Dockerfile runs, as `.../python manage.py NAME`.
DOCKERFILE_COMMAND = re.compile(r"\bpython[ \t]+manage\.py[ \t]+([a-z_]+)")
MESSAGE = (
    "derbynet: manage.py {} refuses to run as root. Run it as the server's "
    "user: pass -u derbynet to docker exec or docker compose exec."
)


def run_as(monkeypatch, tmp_path, argv, *, euid=0, marker=True):
    """manage.py's main() with `argv`, under effective uid `euid`, with the
    image's marker present or not: the argv Django was handed, or the
    SystemExit's message."""
    import django.core.management

    manage = load_manage()
    path = tmp_path / "docker-image"
    if marker:
        path.write_text("marker\n")
    ran = []
    monkeypatch.setattr(manage, "IMAGE_MARKER", path)
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(django.core.management, "execute_from_command_line", ran.append)
    try:
        manage.main()
    except SystemExit as stopped:
        assert ran == [], "Django ran the command before the refusal"
        return str(stopped.code)
    return ran


def test_root_in_the_image_is_refused_with_one_line(monkeypatch, tmp_path):
    for argv, command in (
        (["manage.py", "check"], "check"),
        (["manage.py", "import_derbynet", "x.sqlite3"], "import_derbynet"),
        (["manage.py", "migrate", "--noinput"], "migrate"),
        (["manage.py"], "help"),
        (["manage.py", "--help"], "--help"),
    ):
        got = run_as(monkeypatch, tmp_path, argv)
        assert got == MESSAGE.format(command), argv
        assert len(got.splitlines()) == 1
        assert "-u derbynet" in got


def test_the_builds_commands_run_as_root_in_the_image(monkeypatch, tmp_path):
    for command in ("collectstatic", "build_timer", "build_docs"):
        argv = ["manage.py", command, "--noinput"]
        assert run_as(monkeypatch, tmp_path, argv) == [argv]


def test_root_outside_the_image_runs(monkeypatch, tmp_path):
    """A host's `uv run python manage.py`, root or not, has no marker."""
    argv = ["manage.py", "check"]
    assert run_as(monkeypatch, tmp_path, argv, marker=False) == [argv]


def test_another_user_in_the_image_runs(monkeypatch, tmp_path):
    argv = ["manage.py", "import_derbynet", "x.sqlite3"]
    assert run_as(monkeypatch, tmp_path, argv, euid=999) == [argv]


def test_the_root_commands_are_the_dockerfiles():
    """ROOT_COMMANDS is the commands the Dockerfile runs at build time, no
    more: every other command is refused as root (D103)."""
    built = set(DOCKERFILE_COMMAND.findall(DOCKERFILE.read_text()))
    assert built == {"collectstatic", "build_timer", "build_docs"}
    assert load_manage().ROOT_COMMANDS == built


def test_every_other_command_is_refused_as_root(monkeypatch, tmp_path):
    from django.core.management import get_commands

    manage = load_manage()
    for command in sorted(set(get_commands()) - manage.ROOT_COMMANDS):
        argv = ["manage.py", command]
        assert run_as(monkeypatch, tmp_path, argv) == MESSAGE.format(command)


def test_the_marker_is_the_dockerfiles():
    """The Dockerfile writes the marker manage.py reads, outside the server
    folder a bind mount replaces, before the build's manage.py commands run,
    so they run under the refusal, and copies it into the image."""
    text = DOCKERFILE.read_text()
    marker = str(load_manage().IMAGE_MARKER)
    assert not marker.startswith("/opt/derbynet/server/")
    written = text.index(f"> {marker}\n")
    assert written < DOCKERFILE_COMMAND.search(text).start()
    assert f"COPY --from=static {marker} {marker}\n" in text


def test_a_host_with_no_geteuid_runs(monkeypatch, tmp_path):
    """Windows has no os.geteuid, and never the marker: the marker is read
    first, so manage.py runs there."""
    manage = load_manage()
    monkeypatch.setattr(manage, "IMAGE_MARKER", tmp_path / "docker-image")
    monkeypatch.delattr(os, "geteuid")
    manage.refuse_root(["manage.py", "check"])


def test_the_upload_check_reads_manage_py_marker():
    """The upload health check (#48) knows the image by the marker
    manage.py reads."""
    from django.conf import settings

    assert settings.DOCKER_IMAGE_MARKER == load_manage().IMAGE_MARKER
