import pytest

pytestmark = pytest.mark.django_db


def test_the_documentation_hint_shows_on_an_unlocked_install(client, event):
    assert "data-docs-hint" in client.get("/login/").content.decode()


def test_the_documentation_hint_hides_when_settings_are_locked(client, event, settings):
    settings.LOCK_SETTINGS = True

    assert "data-docs-hint" not in client.get("/login/").content.decode()


def test_the_kiosk_and_camera_entries(client, event):
    page = client.get("/login/").content.decode()

    for label, href in [
        ("Be a Kiosk", "/kiosk.php"),
        ("Fullscreen Kiosk", "/fullscreen.php"),
        ("Replay Kiosk", "/replay/"),
        ("Be a Camera", "/camera.php"),
    ]:
        assert f'href="{href}"' in page
        assert f">{label}</a>" in page


def test_show_all_roles_keeps_the_page_to_return_to(client, event):
    page = client.get("/login/?next=/checkin/").content.decode()

    assert 'href="?all&amp;next=/checkin/"' in page


def test_a_logged_in_role_can_log_out_from_the_login_page(login):
    page = login("RaceCrew").get("/login/").content.decode()

    assert "Logged in as <strong>RaceCrew</strong>." in page
    assert page.count('action="/logout/"') == 2
