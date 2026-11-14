"""The WSGI application the Docker image's gunicorn serves (#58).

gunicorn listens on a unix socket that only the image's nginx can open, so
every request reaches Django with an empty `REMOTE_ADDR`. nginx sets
`X-Real-IP` and `X-Real-Port` to the client's address and port on every
request, replacing any such header the client sent, and this copies them back
to `REMOTE_ADDR` and `REMOTE_PORT`, where the kiosk addresses and `ip_addr`
read them as PHP read `$_SERVER['REMOTE_ADDR']` and `REMOTE_PORT` under
php-fpm. Serve `derbynet.wsgi` instead wherever a client can reach the
WSGI server directly.
"""

from derbynet.wsgi import application as django_application


def application(environ, start_response):
    if client := environ.get("HTTP_X_REAL_IP"):
        environ["REMOTE_ADDR"] = client
    if port := environ.get("HTTP_X_REAL_PORT"):
        environ["REMOTE_PORT"] = port
    return django_application(environ, start_response)
