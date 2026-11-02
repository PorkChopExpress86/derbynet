import os

import pytest


def test_a_default_run_keeps_djangos_async_check(request):
    """M6: only the browser tests turn off Django's async-unsafe check. A run
    that selects a browser test too, such as `-m ""`, turns it off for the
    whole session, so the check applies only to a run without one."""
    if any(item.get_closest_marker("browser") for item in request.session.items):
        pytest.skip("a browser test in this run turns the check off")
    assert "DJANGO_ALLOW_ASYNC_UNSAFE" not in os.environ
