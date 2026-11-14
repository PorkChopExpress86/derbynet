from derbynet import proxied_wsgi
from derbynet.wsgi import application


def seen(monkeypatch, environ):
    got = {}

    def django(environ, start_response):
        got.update(environ)
        return [b""]

    monkeypatch.setattr(proxied_wsgi, "django_application", django)
    proxied_wsgi.application(environ, None)
    return got.get("REMOTE_ADDR"), got.get("REMOTE_PORT")


def test_the_wrapper_serves_the_django_application():
    assert proxied_wsgi.django_application is application


def test_the_client_address_comes_from_x_real_ip_and_port(monkeypatch):
    environ = {
        "REMOTE_ADDR": "",
        "HTTP_X_REAL_IP": "192.168.1.23",
        "HTTP_X_REAL_PORT": "41734",
    }
    assert seen(monkeypatch, environ) == ("192.168.1.23", "41734")


def test_with_no_x_real_headers_the_socket_address_stands(monkeypatch):
    assert seen(monkeypatch, {"REMOTE_ADDR": "10.0.0.5"}) == ("10.0.0.5", None)
