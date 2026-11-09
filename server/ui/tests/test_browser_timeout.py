"""Each browser test carries the root conftest's `BROWSER_TIMEOUT`, and
no other test does (#247 retro: a probe on a paused Playwright clock hung
for about 10 hours)."""

import os

import pytest


def timeout(request):
    given = request.config.getoption("timeout")
    if given is not None or "PYTEST_TIMEOUT" in os.environ:
        pytest.skip("--timeout or PYTEST_TIMEOUT sets every test's bound")
    marker = request.node.get_closest_marker("timeout")
    return marker.args if marker else None


@pytest.mark.browser
def test_a_browser_test_fails_after_the_browser_timeout(request):
    (seconds,) = timeout(request)
    assert seconds > 0


def test_other_tests_have_no_timeout(request):
    assert timeout(request) is None
