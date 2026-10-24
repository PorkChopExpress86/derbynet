"""Django settings for the DerbyNet server.

Every install-level choice comes from an environment variable, documented in
server/README.md.
"""

import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_dir(name, default):
    path = Path(os.environ.get(name) or default).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env_list(name, default=""):
    return [
        item.strip()
        for item in os.environ.get(name, default).split(",")
        if item.strip()
    ]


def _env_flag(name):
    value = os.environ.get(name, "0")
    if value not in ("0", "1"):
        raise ImproperlyConfigured(f"{name} must be 0 or 1, not {value!r}")
    return value == "1"


def _url_prefix(value):
    value = value.strip().strip("/")
    return f"/{value}" if value else None


def _is_time_zone(name):
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError, OSError:
        return False
    return True


def _time_zone():
    configured = os.environ.get("DERBYNET_TIME_ZONE")
    if configured:
        if not _is_time_zone(configured):
            raise ImproperlyConfigured(
                f"DERBYNET_TIME_ZONE must be an IANA time zone name, not {configured!r}"
            )
        return configured
    # Hosts often set TZ to something else, such as ":/etc/localtime".
    host = os.environ.get("TZ", "")
    return host if host and _is_time_zone(host) else "UTC"


def _stored_secret_key(config_dir):
    path = config_dir / "secret_key"
    if not path.exists():
        # Two processes can start at once, as runserver's reloader does. Each
        # writes a complete key to its own file, and only the first link wins.
        fd, temp = tempfile.mkstemp(dir=config_dir)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(get_random_secret_key())
            os.link(temp, path)
        except FileExistsError:
            pass
        finally:
            os.unlink(temp)
    key = path.read_text().strip()
    if not key:
        raise ImproperlyConfigured(
            f"{path} is empty. Delete it to generate a new key, "
            "or set DJANGO_SECRET_KEY."
        )
    return key


DATA_DIR = _env_dir("DERBYNET_DATA_DIR", BASE_DIR / "var" / "data")
CONFIG_DIR = _env_dir("DERBYNET_CONFIG_DIR", BASE_DIR / "var" / "config")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or _stored_secret_key(CONFIG_DIR)

DEBUG = _env_flag("DERBYNET_DEBUG")

# A legacy handler that writes a session it did not declare raises when this
# is on, and the write is dropped with a warning when it is off. Tests turn it
# on in conftest.py.
LEGACY_SESSION_WRITES_RAISE = DEBUG

ALLOWED_HOSTS = _env_list("DERBYNET_ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = _env_list("DERBYNET_CSRF_TRUSTED_ORIGINS")

FORCE_SCRIPT_NAME = _url_prefix(os.environ.get("DERBYNET_URL_PREFIX", ""))

# Plain HTTP on a LAN is the common install, so cookies and redirects stay
# HTTP-friendly. The bundled proxy says when the client used HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# No django.contrib.auth or admin: roles live in the session (ADR-16).
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "roster",
    "racing",
    "awards",
    "kiosks",
    "media",
    "transfer",
    "legacy_api",
    "ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "derbynet.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "derbynet.wsgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'derbynet.sqlite3'}"
    ),
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # WAL lets kiosk and timer reads run during a write. IMMEDIATE takes the
    # write lock at BEGIN, so concurrent writers wait instead of failing with
    # SQLITE_BUSY partway through a transaction.
    DATABASES["default"]["OPTIONS"] = {
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        "transaction_mode": "IMMEDIATE",
        "timeout": 20,
    }
    # An in-memory test database cannot use WAL, so tests use a file.
    DATABASES["default"]["TEST"] = {"NAME": str(DATA_DIR / "test-derbynet.sqlite3")}

LANGUAGE_CODE = "en-us"
TIME_ZONE = _time_zone()
USE_I18N = True
USE_TZ = True

# Relative, so Django puts FORCE_SCRIPT_NAME in front of it.
STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
