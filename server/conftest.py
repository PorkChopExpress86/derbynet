def pytest_configure():
    from django.conf import settings

    settings.LEGACY_SESSION_WRITES_RAISE = True
