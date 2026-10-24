import stat

import dj_database_url
import pytest
from django.core.exceptions import ImproperlyConfigured


def test_secret_key_is_generated_once_with_mode_0600(load_settings, tmp_path):
    config_dir = tmp_path / "empty-config"
    config_dir.mkdir()

    first = load_settings(DERBYNET_CONFIG_DIR=str(config_dir))

    key_file = config_dir / "secret_key"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert key_file.read_text() == first["SECRET_KEY"]
    assert len(first["SECRET_KEY"]) >= 50

    second = load_settings(DERBYNET_CONFIG_DIR=str(config_dir))
    assert second["SECRET_KEY"] == first["SECRET_KEY"]
    assert [p.name for p in config_dir.iterdir()] == ["secret_key"]


def test_django_secret_key_overrides_the_file(load_settings, tmp_path):
    loaded = load_settings(DJANGO_SECRET_KEY="from-the-environment")

    assert loaded["SECRET_KEY"] == "from-the-environment"
    assert not (tmp_path / "config" / "secret_key").exists()


def test_sqlite_is_the_default_in_wal_mode(load_settings, tmp_path):
    db = load_settings()["DATABASES"]["default"]

    assert db["ENGINE"] == "django.db.backends.sqlite3"
    assert db["NAME"] == str(tmp_path / "data" / "derbynet.sqlite3")
    assert db["OPTIONS"] == {
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        "transaction_mode": "IMMEDIATE",
        "timeout": 20,
    }


def test_postgres_url_passes_through_unchanged(load_settings):
    url = "postgres://derby:secret@db.example:5432/derbynet"

    db = load_settings(DATABASE_URL=url)["DATABASES"]["default"]

    assert db == dj_database_url.parse(url)
    assert db["ENGINE"] == "django.db.backends.postgresql"


@pytest.mark.parametrize(
    ("prefix", "script_name"),
    [("", None), ("/derbynet", "/derbynet"), ("derbynet/", "/derbynet")],
)
def test_url_prefix_sets_force_script_name(load_settings, prefix, script_name):
    loaded = load_settings(DERBYNET_URL_PREFIX=prefix)

    assert loaded["FORCE_SCRIPT_NAME"] == script_name


def test_allowed_hosts_default_to_any_lan_address(load_settings):
    assert load_settings()["ALLOWED_HOSTS"] == ["*"]
    assert load_settings(DERBYNET_ALLOWED_HOSTS="race.local, 10.0.0.5")[
        "ALLOWED_HOSTS"
    ] == ["race.local", "10.0.0.5"]


def test_debug_rejects_values_other_than_0_or_1(load_settings):
    assert load_settings(DERBYNET_DEBUG="1")["DEBUG"] is True
    with pytest.raises(ImproperlyConfigured, match="DERBYNET_DEBUG"):
        load_settings(DERBYNET_DEBUG="yes")


def test_time_zone_falls_back_to_tz_then_utc(load_settings, monkeypatch):
    monkeypatch.delenv("DERBYNET_TIME_ZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    assert load_settings()["TIME_ZONE"] == "UTC"
    assert load_settings(TZ="America/Chicago")["TIME_ZONE"] == "America/Chicago"
    assert (
        load_settings(TZ="America/Chicago", DERBYNET_TIME_ZONE="Europe/London")[
            "TIME_ZONE"
        ]
        == "Europe/London"
    )
