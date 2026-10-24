from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password


def test_password_hashing_works_without_the_auth_app():
    assert "django.contrib.auth" not in settings.INSTALLED_APPS

    stored = make_password("doyourbest")

    assert stored != "doyourbest"
    assert check_password("doyourbest", stored)
    assert not check_password("wrong", stored)
