"""`PlainHttpCoop`: no Cross-Origin-Opener-Policy header where the browser
ignores it anyway.

Why it exists: most race venues serve DerbyNet over plain HTTP on the LAN,
at an address such as 192.168.1.20. A browser ignores COOP from an origin
that is not potentially trustworthy, and Chrome logs a console error on
every page that sends it there (#42: check-in must log no console error over
plain HTTP). So the header goes only where it does nothing.

Where a browser applies COOP the header stays, so security is unchanged:
over HTTPS (or behind a proxy that says so, `SECURE_PROXY_SSL_HEADER`), and
on the hosts browsers trust over plain HTTP: `localhost`, `*.localhost`,
127.0.0.0/8 and [::1].
"""

import ipaddress

COOP = "Cross-Origin-Opener-Policy"


def _hostname(host_and_port: str) -> str:
    """The host of `Host`, without its port: `[::1]:8000` is `::1`."""
    if host_and_port.startswith("["):
        return host_and_port[1 : host_and_port.find("]")]
    return host_and_port.split(":", 1)[0]


def trustworthy(host_and_port: str) -> bool:
    """A host a browser treats as a secure context over plain HTTP."""
    host = _hostname(host_and_port).lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class PlainHttpCoop:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            COOP in response
            and not request.is_secure()
            and not trustworthy(request.get_host())
        ):
            del response[COOP]
        return response
