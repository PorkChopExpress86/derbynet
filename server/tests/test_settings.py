import runpy
import stat
from pathlib import Path

import dj_database_url
import pytest
from django.core.exceptions import ImproperlyConfigured

import derbynet.settings


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


# ----- the compose postgres service (#308) -----------------------------------
# compose.yaml's server environment, with a password holding every character
# a URL reserves that checks.sh postgres-password tries.
COMPOSE = {
    "DERBYNET_POSTGRES_HOST": "postgres",
    "POSTGRES_USER": "derbynet",
    "POSTGRES_PASSWORD": "p@ss:w/rd#%",
    "POSTGRES_DB": "derbynet",
}


def engine_file(tmp_path, engine):
    """database-engine as the entrypoint writes it: `echo "$engine" > ...`."""
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "database-engine").write_text(f"{engine}\n")


def test_a_postgres_volume_under_compose_uses_the_postgres_service(
    load_settings, tmp_path, monkeypatch
):
    """What gunicorn, migrate and an exec'd manage.py get on a volume the
    entrypoint marked Postgres: no name is looked up, so with Postgres down a
    command fails to connect rather than falling back to SQLite."""
    engine_file(tmp_path, "postgres")

    def no_lookup(*args, **kwargs):
        raise AssertionError("settings looked a name up")

    monkeypatch.setattr("socket.getaddrinfo", no_lookup)
    monkeypatch.setattr("socket.gethostbyname", no_lookup)
    db = load_settings(**COMPOSE)["DATABASES"]["default"]

    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert (db["HOST"], db["PORT"]) == ("postgres", 5432)
    assert (db["USER"], db["PASSWORD"], db["NAME"]) == (
        "derbynet",
        "p@ss:w/rd#%",
        "derbynet",
    )
    assert "OPTIONS" not in db or db["OPTIONS"] == {}


@pytest.mark.parametrize("engine", ["sqlite", "other", None])
def test_compose_without_a_postgres_volume_keeps_sqlite(
    load_settings, tmp_path, engine
):
    """compose.yaml sets DERBYNET_POSTGRES_HOST with or without its profile,
    so the host alone picks nothing. None is a volume with no database-engine
    yet: a fresh one before its first start."""
    if engine:
        engine_file(tmp_path, engine)

    db = load_settings(**COMPOSE)["DATABASES"]["default"]

    assert db["ENGINE"] == "django.db.backends.sqlite3"
    assert db["NAME"] == str(tmp_path / "data" / "derbynet.sqlite3")


def test_a_postgres_volume_without_the_compose_host_keeps_sqlite(
    load_settings, tmp_path
):
    engine_file(tmp_path, "postgres")
    env = {k: v for k, v in COMPOSE.items() if k != "DERBYNET_POSTGRES_HOST"}

    db = load_settings(**env)["DATABASES"]["default"]

    assert db["ENGINE"] == "django.db.backends.sqlite3"
    assert db["NAME"] == str(tmp_path / "data" / "derbynet.sqlite3")


def test_an_explicit_database_url_wins_over_the_postgres_service(
    load_settings, tmp_path
):
    engine_file(tmp_path, "postgres")
    url = "postgres://other:secret@db.example:5433/elsewhere"

    db = load_settings(DATABASE_URL=url, **COMPOSE)["DATABASES"]["default"]

    assert db == dj_database_url.parse(url)


@pytest.mark.parametrize("name", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
def test_a_missing_postgres_variable_is_named_without_the_password(
    load_settings, tmp_path, name
):
    engine_file(tmp_path, "postgres")
    env = {k: v for k, v in COMPOSE.items() if k != name}

    with pytest.raises(ImproperlyConfigured) as raised:
        load_settings(**env)

    assert str(raised.value).startswith(f"{name} must be set")
    assert COMPOSE["POSTGRES_PASSWORD"] not in str(raised.value)


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
    ] == ["race.local", "10.0.0.5", "localhost", "127.0.0.1", "[::1]"]


def test_named_hosts_keep_the_server_itself(load_settings):
    """The image's HEALTHCHECK asks for http://127.0.0.1/, and a browser on
    the server machine for localhost, so naming a host keeps them (#61: a
    named host turned the container unhealthy). Each is listed once."""
    hosts = load_settings(DERBYNET_ALLOWED_HOSTS="derby.example.org,localhost")[
        "ALLOWED_HOSTS"
    ]
    assert hosts == ["derby.example.org", "localhost", "127.0.0.1", "[::1]"]


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


@pytest.mark.parametrize("tz", [":/etc/localtime", "Bogus/Zone"])
def test_host_tz_that_is_not_an_iana_name_falls_back_to_utc(
    load_settings, monkeypatch, tz
):
    monkeypatch.delenv("DERBYNET_TIME_ZONE", raising=False)

    assert load_settings(TZ=tz)["TIME_ZONE"] == "UTC"


def test_invalid_derbynet_time_zone_names_the_variable_and_value(load_settings):
    with pytest.raises(ImproperlyConfigured, match="DERBYNET_TIME_ZONE.*'Mars/Base'"):
        load_settings(DERBYNET_TIME_ZONE="Mars/Base")


def test_use_tz_is_on(load_settings):
    assert load_settings()["USE_TZ"] is True


def test_csrf_trusted_origins_come_from_the_environment(load_settings):
    assert load_settings()["CSRF_TRUSTED_ORIGINS"] == []
    loaded = load_settings(
        DERBYNET_CSRF_TRUSTED_ORIGINS="https://a.example, https://b.example"
    )
    assert loaded["CSRF_TRUSTED_ORIGINS"] == ["https://a.example", "https://b.example"]


def test_default_data_and_config_dirs_live_under_server_var(monkeypatch):
    monkeypatch.delenv("DERBYNET_DATA_DIR", raising=False)
    monkeypatch.delenv("DERBYNET_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    server_dir = Path(derbynet.settings.__file__).resolve().parent.parent

    loaded = runpy.run_path(derbynet.settings.__file__)

    assert loaded["DATA_DIR"] == server_dir / "var" / "data"
    assert loaded["CONFIG_DIR"] == server_dir / "var" / "config"


def test_empty_stored_secret_key_is_rejected(load_settings, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "secret_key").write_text("")

    with pytest.raises(ImproperlyConfigured, match="secret_key.*empty"):
        load_settings()


def test_static_root_comes_from_the_environment(load_settings, tmp_path):
    assert load_settings()["STATIC_ROOT"] is None
    root = str(tmp_path / "static")
    assert load_settings(DERBYNET_STATIC_ROOT=root)["STATIC_ROOT"] == root


@pytest.mark.parametrize(
    ("value", "size"),
    [("16M", 16 * 1024**2), ("512k", 512 * 1024), ("1G", 1024**3), ("2000", 2000)],
)
def test_max_upload_size_reads_nginx_sizes(load_settings, value, size):
    loaded = load_settings(MAX_UPLOAD_SIZE=value)
    assert loaded["DATA_UPLOAD_MAX_MEMORY_SIZE"] == size


def test_max_upload_size_defaults_to_100m(load_settings):
    assert load_settings()["DATA_UPLOAD_MAX_MEMORY_SIZE"] == 100 * 1024**2


@pytest.mark.parametrize("value", ["16MB", "M", "-1M", "0", "1.5M", "sixteen"])
def test_max_upload_size_refuses_what_nginx_would_not_read(load_settings, value):
    with pytest.raises(ImproperlyConfigured, match="MAX_UPLOAD_SIZE"):
        load_settings(MAX_UPLOAD_SIZE=value)
