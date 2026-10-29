"""`post-timer-log.php` and `query=timer.log` (#32, S22)."""

import json

import pytest
from django.test import Client, override_settings

from accounts.permissions import Perm
from legacy_api.tests.conftest import log_in, post
from racing.models import TimerStatus

pytestmark = pytest.mark.django_db

PROLOG = '<?xml version="1.0" encoding="UTF-8"?>\n'


@pytest.fixture
def logs(event, tmp_path, settings):
    from core.models import RaceSettings

    RaceSettings.objects.filter(event=event).update(logs_directory=str(tmp_path))
    return tmp_path


@pytest.fixture
def jar(event):
    jar = Client(headers={"User-Agent": "DerbyNet-Timer/test"})
    log_in(jar, Perm.TIMER_MESSAGE, role="Timer")
    return jar


@pytest.fixture
def coordinator(client):
    post(client, "action=role.login&name=RaceCoordinator&password=doyourbest")
    return client


def upload(client, data: bytes, path="/post-timer-log.php"):
    return client.post(path, data, content_type="text/plain")


def tail(client, seek=0) -> dict:
    return json.loads(client.get(f"/action.php?query=timer.log&seek={seek}").content)


def test_a_log_upload_appends_and_reads_back(logs, jar, coordinator):
    post(jar, "action=timer-message&message=HELLO")
    first = upload(jar, b"line one\n")
    assert first.status_code == 200
    assert first["Content-Type"] == "text/xml; charset=utf-8"
    assert first.content.decode() == PROLOG + "<success>9 bytes</success>\n"
    upload(jar, b"line two\n", path="/derbynet/post-timer-log.php")

    found = tail(coordinator)
    assert found["file-data"] == "line one\nline two\n"
    assert found["file-size"] == 18
    assert found["log-file-name"].startswith(str(logs))
    later = tail(coordinator, seek=9)
    assert (later["file-data"], later["file-size"]) == ("line two\n", 18)
    assert "file-data" not in tail(coordinator, seek=18)
    assert tail(coordinator, seek=99)["file-size"] == 0


def test_a_log_upload_without_hello_starts_a_log(logs, jar, coordinator):
    assert upload(jar, b"x").content.decode().endswith("<success>1 bytes</success>\n")
    assert tail(coordinator)["file-data"] == "x"


def test_a_log_upload_needs_the_timer_permission(logs, event):
    """S22: PHP appends anyone's upload."""
    response = upload(Client(), b"spoofed")
    assert response.status_code == 200
    assert b"notauthorized" in response.content
    assert list(logs.iterdir()) == []


def test_a_stored_path_outside_the_logs_folder_is_never_opened(
    logs, jar, coordinator, tmp_path_factory, event
):
    outside = tmp_path_factory.mktemp("elsewhere") / "timer-20260101-1200-00.log"
    outside.write_text("secret")
    TimerStatus.objects.filter(event=event).update(log_path=str(outside))
    found = tail(coordinator)
    assert found["outcome"]["code"] == "nolog"
    assert found["file-size"] == 0
    upload(jar, b"new")
    assert outside.read_text() == "secret"
    assert tail(coordinator)["file-data"] == "new"


def test_a_locked_install_hides_the_log_path(logs, jar, coordinator):
    upload(jar, b"x")
    with override_settings(LOCK_SETTINGS=True):
        assert "log-file-name" not in tail(coordinator)


def test_a_get_is_not_a_page(event):
    response = Client().get("/post-timer-log.php")
    assert b"This is not a page." in response.content
