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


def test_after_the_timeout_fires_the_harness_reads_nothing_from_the_page():
    """The timer fires once, so a read of a page frozen in a script loop
    after it would wait for ever (#250 retro). The harness marks the page,
    and its teardown skips no_sideways_scroll."""
    from types import SimpleNamespace

    import pytest_timeout

    from ui.tests.browser.conftest import _timed_out

    fired = pytest.fail.Exception(pytest_timeout.PYTEST_FAILURE_MESSAGE % 300.0)
    page = SimpleNamespace()
    assert _timed_out(page, fired)
    assert page.timed_out is True

    other = SimpleNamespace()
    assert not _timed_out(other, pytest.fail.Exception("a check failed"))
    assert not _timed_out(other, AssertionError(str(fired)))
    assert not hasattr(other, "timed_out")
