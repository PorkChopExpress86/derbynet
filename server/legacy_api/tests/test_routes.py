import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]

# Django reads the prefix once at startup, so each case runs in a fresh process.
SCRIPT = """
import json, sys
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0)
from core.services import new_event
new_event("Probe")
from django.test import Client
client = Client(enforce_csrf_checks=True)
statuses = {
    path: client.post(path, "action=no.such",
                      content_type="application/x-www-form-urlencoded").status_code
    for path in sys.argv[1:]
}
print(json.dumps(statuses))
"""


def statuses(tmp_path, prefix, *paths):
    env = os.environ | {
        "DJANGO_SETTINGS_MODULE": "derbynet.settings",
        "DERBYNET_URL_PREFIX": prefix,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'db.sqlite3'}",
        "DERBYNET_DATA_DIR": str(tmp_path / "data"),
        "DERBYNET_CONFIG_DIR": str(tmp_path / "config"),
    }
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, *paths],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("", {"/action.php": 200, "/derbynet/action.php": 200}),
        ("/derbynet", {"/action.php": 200, "/derbynet/action.php": 200}),
        (
            "/pinewood",
            {
                "/action.php": 200,
                "/pinewood/action.php": 200,
                "/derbynet/action.php": 404,
            },
        ),
    ],
)
def test_action_php_answers_without_a_redirect(tmp_path, prefix, expected):
    assert statuses(tmp_path, prefix, *expected) == expected
