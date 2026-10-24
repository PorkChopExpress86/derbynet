import pytest
from django.db import connection


@pytest.mark.django_db
def test_default_sqlite_connection_uses_wal():
    if connection.vendor != "sqlite":
        pytest.skip("WAL is a SQLite journal mode")

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode")
        assert cursor.fetchone()[0] == "wal"
        cursor.execute("PRAGMA synchronous")
        assert cursor.fetchone()[0] == 1  # NORMAL
