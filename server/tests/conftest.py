import runpy

import pytest

import derbynet.settings


@pytest.fixture
def load_settings(monkeypatch, tmp_path):
    """Run settings.py afresh under the given environment and return its names.

    The data and config directories default to fresh temporary directories, so
    a load never touches server/var.
    """

    def load(**env):
        env.setdefault("DERBYNET_DATA_DIR", str(tmp_path / "data"))
        env.setdefault("DERBYNET_CONFIG_DIR", str(tmp_path / "config"))
        for name in ("DJANGO_SECRET_KEY", "DATABASE_URL", "DERBYNET_URL_PREFIX"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return runpy.run_path(derbynet.settings.__file__)

    return load
