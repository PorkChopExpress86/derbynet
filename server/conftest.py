import os

import pytest

# Each browser test, fixtures included, fails after this many seconds, so a
# hang ends the run. #247's probe awaited axe on a paused Playwright clock;
# `page.evaluate` has no timeout, and the run sat for about 10 hours. The
# slowest browser test took 31 s with 8 workers (test_smoke.py, phone), so
# this leaves about ten times that for CI's smaller runners and
# tools/pytest-under-load.sh. `--timeout N` or PYTEST_TIMEOUT sets every
# test's bound instead, and `--timeout 0` turns it off (#247 retro).
BROWSER_TIMEOUT = 300


def pytest_configure():
    from django.conf import settings

    settings.LEGACY_SESSION_WRITES_RAISE = True


@pytest.fixture(autouse=True)
def _data_dir(settings, tmp_path_factory):
    """Each test's data folder (event folders, photo locks) is its own, never
    `server/var/data`. A full run wrote some 250 folders and files there,
    which every xdist worker and a dev server shared: in #212 a file
    comparison saw another worker's upload (#212 retro). A test that needs
    the folder at a known path sets `settings.DATA_DIR` itself. It sits
    beside `tmp_path`, not in it, so a test that lists or fills `tmp_path`
    finds only its own files."""
    settings.DATA_DIR = tmp_path_factory.mktemp("derbynet-data")


ID_OFFSET_STEP = 1000


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Each table's ids start at their own multiple of ID_OFFSET_STEP, never
    at 1, on SQLite as on Postgres. In a fresh SQLite test database a row's
    id equalled its position, so an export that wrote a database id where
    the importer reads a position passed every SQLite test and failed only
    on Postgres (#221's RaceInfo ids, #221 retro). A start of its own per
    table also fails a test that passes one table's id as another's."""
    from django.apps import apps
    from django.db import connection, models

    tables = sorted(
        (model._meta.db_table, model._meta.pk.column)
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and isinstance(model._meta.pk, models.AutoField)
    )
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        for n, (table, column) in enumerate(tables, start=1):
            start = n * ID_OFFSET_STEP
            if connection.vendor == "sqlite":
                cursor.execute("DELETE FROM sqlite_sequence WHERE name = %s", [table])
                cursor.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES (%s, %s)",
                    [table, start],
                )
            else:
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), %s)",
                    [table, column, start],
                )


def pytest_collection_modifyitems(items):
    from racing.engine.tests.golden import reads_golden_fixtures

    golden = {}
    for item in items:
        module = getattr(item, "module", None)
        if module is None:
            continue
        if module.__name__ not in golden:
            golden[module.__name__] = reads_golden_fixtures(module)
        if golden[module.__name__]:
            item.add_marker(pytest.mark.golden)


def pytest_collection_finish(session):
    """After every conftest's pytest_collection_modifyitems, so the browser
    marker that ui/tests/browser/conftest.py adds is on."""
    if (
        session.config.getoption("timeout") is not None
        or "PYTEST_TIMEOUT" in os.environ
    ):
        return
    for item in session.items:
        bounded = item.get_closest_marker("timeout")
        if item.get_closest_marker("browser") and not bounded:
            item.add_marker(pytest.mark.timeout(BROWSER_TIMEOUT))
