"""COOP only where the browser applies it (#42)."""

import pytest

pytestmark = pytest.mark.django_db


def test_plain_http_from_the_lan_gets_no_coop_header(client):
    assert "Cross-Origin-Opener-Policy" not in client.get("/login/")


def test_https_keeps_the_coop_header(client):
    response = client.get("/login/", secure=True)
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


def test_a_proxy_that_terminates_https_keeps_it(client):
    response = client.get("/login/", HTTP_X_FORWARDED_PROTO="https")
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8000",
        "127.0.0.1:8000",
        "127.1.2.3",
        "[::1]:8000",
        "app.localhost",
    ],
)
def test_a_trustworthy_plain_http_host_keeps_the_coop_header(client, host):
    """Browsers treat these hosts as secure over plain HTTP and apply COOP."""
    response = client.get("/login/", HTTP_HOST=host)
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


@pytest.mark.parametrize("host", ["192.168.1.20:8000", "derbynet.local", "testserver"])
def test_a_lan_host_over_plain_http_gets_none(client, host):
    assert "Cross-Origin-Opener-Policy" not in client.get("/login/", HTTP_HOST=host)
