from unittest import mock

import pytest
from django.test import RequestFactory, override_settings

from ui.server_urls import preferred_urls


@pytest.fixture
def config_dir(tmp_path, settings):
    settings.CONFIG_DIR = tmp_path
    return tmp_path


def request(host="10.0.0.5:8000", secure=False, **headers):
    return RequestFactory().get(
        "/about/", headers=headers, HTTP_HOST=host, secure=secure
    )


def addresses(*found):
    return mock.patch(
        "ui.server_urls.socket.gethostbyname_ex", return_value=("host", [], list(found))
    )


def test_the_config_url_file_wins(config_dir):
    (config_dir / "config-url").write_text("http://my-host.com/with/a/long/path\n")

    with addresses("192.168.1.20"):
        assert preferred_urls(request()) == ["http://my-host.com/with/a/long/path"]
        assert preferred_urls(request(), use_https=True) == [
            "http://my-host.com/with/a/long/path"
        ]


def test_without_the_file_each_host_address_makes_a_url(config_dir):
    with addresses("127.0.1.1", "192.168.1.20", "10.0.0.5"):
        urls = preferred_urls(request())

    assert urls == ["http://192.168.1.20:8000", "http://10.0.0.5:8000"]


def test_the_default_port_is_left_out(config_dir):
    with addresses("192.168.1.20"):
        assert preferred_urls(request(host="192.168.1.20")) == ["http://192.168.1.20"]


def test_https_can_be_forced(config_dir):
    with addresses("192.168.1.20"):
        assert preferred_urls(request(), use_https=True) == ["https://192.168.1.20"]


def test_behind_a_proxy_the_host_header_names_the_server(config_dir):
    proxied = request(host="derby.example", X_FORWARDED_FOR="203.0.113.9")

    with addresses("172.17.0.2"):
        assert preferred_urls(proxied) == ["http://derby.example"]
        assert preferred_urls(proxied, use_https=True) == ["https://derby.example"]


def test_a_host_with_only_loopback_falls_back_to_the_host_header(config_dir):
    with addresses("127.0.0.1"):
        assert preferred_urls(request(host="pinewood.local:8000")) == [
            "http://pinewood.local:8000"
        ]


@override_settings(FORCE_SCRIPT_NAME="/derbynet")
def test_the_url_prefix_is_kept(config_dir):
    from django.urls import set_script_prefix

    set_script_prefix("/derbynet/")
    try:
        with addresses("192.168.1.20"):
            assert preferred_urls(request(host="192.168.1.20")) == [
                "http://192.168.1.20/derbynet"
            ]
    finally:
        set_script_prefix("/")
