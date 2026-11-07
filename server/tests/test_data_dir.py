"""Every test gets a data folder of its own (root `conftest.py`, #212 retro).

With `server/var/data` shared, a full run wrote some 250 event folders and
photo locks there, and in #212 one xdist worker's file comparison saw
another worker's upload.
"""

from pathlib import Path

from django.conf import settings as django_settings

SHARED = Path(django_settings.BASE_DIR) / "var" / "data"


def test_a_test_writes_into_a_data_folder_of_its_own(tmp_path):
    data = Path(django_settings.DATA_DIR)
    assert data.is_dir()
    assert list(data.iterdir()) == []
    assert SHARED not in [data, *data.parents]
    assert tmp_path not in [data, *data.parents]
