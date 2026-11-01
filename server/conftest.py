import pytest


def pytest_configure():
    from django.conf import settings

    settings.LEGACY_SESSION_WRITES_RAISE = True


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
