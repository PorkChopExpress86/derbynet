import pytest


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
