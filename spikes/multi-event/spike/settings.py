"""Throwaway spike settings. SPIKE_MODE picks the event model, SPIKE_DB the engine.

SPIKE_MODE=a  one database per event: `site` alias plus an `event` alias that
              the middleware repoints when SiteState.version changes.
SPIKE_MODE=b  one database: Event rows, SiteState.active_event, event FK.
SPIKE_DB=sqlite|pg
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MODE = os.environ.get("SPIKE_MODE", "a")
DB = os.environ.get("SPIKE_DB", "sqlite")
DATA_DIR = Path(os.environ.get("SPIKE_DATA", BASE_DIR / ".data" / "default"))
PG_PORT = os.environ.get("SPIKE_PG_PORT", "8090")
FAULT = os.environ.get("SPIKE_FAULT", "")

SECRET_KEY = "spike-not-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "spike.urls"
WSGI_APPLICATION = "spike.wsgi.application"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR"}}}


def sqlite(name):
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(name),
        "CONN_MAX_AGE": None,
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }


def pg(name):
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": "postgres",
        "PASSWORD": "spike",
        "HOST": "127.0.0.1",
        "PORT": PG_PORT,
        "CONN_MAX_AGE": None,
    }


def event_db(slug):
    """Connection settings for one event's database in option (a)."""
    if DB == "pg":
        return pg(f"spike_ev_{slug}")
    return sqlite(DATA_DIR / "events" / slug / "derbynet.sqlite3")


if MODE == "a":
    INSTALLED_APPS = ["siteapp", "eventapp"]
    MIDDLEWARE = ["spike.middleware.repoint_event_db"]
    DATABASE_ROUTERS = ["spike.router.SiteEventRouter"]
    DATABASES = {
        "default": {},
        "site": pg("spike_site") if DB == "pg" else sqlite(DATA_DIR / "site.sqlite3"),
        # NAME is a placeholder; the middleware sets it from SiteState on the first request.
        "event": event_db("unset"),
    }
else:
    INSTALLED_APPS = ["bapp"]
    MIDDLEWARE = ["spike.middleware.resolve_active_event"]
    DATABASES = {"default": pg("spike_b") if DB == "pg" else sqlite(DATA_DIR / "b.sqlite3")}
