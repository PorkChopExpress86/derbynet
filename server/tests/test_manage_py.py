"""manage.py when the compose postgres service does not answer (#308).

A command run with `docker compose exec` while the postgres container is
stopped prints a two-line message naming the host and what to start, not
Django's traceback. The host here, under `.invalid`, never resolves (RFC
6761), as a stopped compose service's name does not.
"""

import os
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
