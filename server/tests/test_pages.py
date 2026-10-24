import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

SERVER_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_health_page_renders_a_url_a_static_path_and_a_csrf_token(client):
    response = client.get("/health/")

    page = response.content.decode()
    assert response.status_code == 200
    assert 'action="/health/"' in page
    assert 'href="/static/core/health.css"' in page
    assert 'name="csrfmiddlewaretoken"' in page


def test_health_page_under_the_url_prefix_has_prefixed_urls_and_static_paths(
    tmp_path,
):
    # Django reads the prefix once at startup, so a fresh process sets it.
    script = (
        "import django; django.setup()\n"
        "from django.core.management import call_command\n"
        "call_command('migrate', verbosity=0)\n"
        "from django.test import Client\n"
        "response = Client().get('/health/')\n"
        "print(response.status_code)\n"
        "print(response.content.decode())\n"
    )
    env = os.environ | {
        "DJANGO_SETTINGS_MODULE": "derbynet.settings",
        "DERBYNET_URL_PREFIX": "/derbynet",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'db.sqlite3'}",
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
    assert 'action="/derbynet/health/"' in page
    assert 'href="/derbynet/static/core/health.css"' in page


@pytest.mark.django_db
def test_health_page_writes_no_session(client):
    from django.contrib.sessions.models import Session

    client.get("/health/")

    assert "sessionid" not in client.cookies
    assert Session.objects.count() == 0


@pytest.mark.django_db
def test_plain_http_gets_no_https_redirect_secure_cookie_or_hsts(client):
    health = client.get("/health/")
    with override_settings(ROOT_URLCONF="tests.session_urls"):
        session = client.get("/session/")

    assert health.status_code == 200
    assert not health.cookies["csrftoken"]["secure"]
    assert "Strict-Transport-Security" not in health.headers
    assert session.status_code == 200
    assert not session.cookies["sessionid"]["secure"]


@pytest.mark.django_db
def test_cookies_stay_insecure_and_hsts_stays_off_behind_https(client):
    health = client.get("/health/", HTTP_X_FORWARDED_PROTO="https")
    with override_settings(ROOT_URLCONF="tests.session_urls"):
        session = client.get("/session/", HTTP_X_FORWARDED_PROTO="https")

    assert health.status_code == 200
    assert not health.cookies["csrftoken"]["secure"]
    assert "Strict-Transport-Security" not in health.headers
    assert session.status_code == 200
    assert not session.cookies["sessionid"]["secure"]
    assert "Strict-Transport-Security" not in session.headers


def test_forwarded_proto_https_makes_the_request_secure(rf):
    request = rf.get("/", HTTP_X_FORWARDED_PROTO="https")

    assert request.is_secure()
    assert not rf.get("/").is_secure()
