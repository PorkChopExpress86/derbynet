"""COOP only where the browser applies it (#42)."""

import pytest

pytestmark = pytest.mark.django_db


def test_plain_http_gets_no_coop_header(client):
    assert "Cross-Origin-Opener-Policy" not in client.get("/login/")


def test_https_keeps_the_coop_header(client):
    response = client.get("/login/", secure=True)
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"


def test_a_proxy_that_terminates_https_keeps_it(client):
    response = client.get("/login/", HTTP_X_FORWARDED_PROTO="https")
    assert response["Cross-Origin-Opener-Policy"] == "same-origin"
