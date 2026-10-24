import os
import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent


def test_admin_login_page_renders(client):
    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert 'action="/admin/login/"' in response.content.decode()


def test_admin_login_posts_under_the_url_prefix(tmp_path):
    # Django reads the prefix once at startup, so a fresh process sets it.
    script = (
        "import django; django.setup()\n"
        "from django.test import Client\n"
        "response = Client().get('/admin/login/')\n"
        "print(response.status_code)\n"
        "print(response.content.decode())\n"
    )
    env = os.environ | {
        "DJANGO_SETTINGS_MODULE": "derbynet.settings",
        "DERBYNET_URL_PREFIX": "/derbynet",
        "DERBYNET_DATA_DIR": str(tmp_path / "data"),
        "DERBYNET_CONFIG_DIR": str(tmp_path / "config"),
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    status, page = result.stdout.split("\n", 1)
    assert status == "200"
    assert 'action="/derbynet/admin/login/"' in page
    assert "/derbynet/static/admin/css/" in page
