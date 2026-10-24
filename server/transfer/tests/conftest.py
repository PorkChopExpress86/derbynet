import pytest


@pytest.fixture(autouse=True)
def data_dir(settings, tmp_path):
    """Each test's event folders go under its own temporary directory."""
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
    return settings.DATA_DIR
