import os


def test_a_default_run_keeps_djangos_async_check():
    """M6: only the browser tests turn off Django's async-unsafe check."""
    assert "DJANGO_ALLOW_ASYNC_UNSAFE" not in os.environ
