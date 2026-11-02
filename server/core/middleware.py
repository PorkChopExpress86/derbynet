"""`PlainHttpCoop`: no Cross-Origin-Opener-Policy header over plain HTTP.

Most race venues serve DerbyNet over plain HTTP on the LAN. A browser
ignores COOP from an origin that is not potentially trustworthy, and Chrome
logs a console error for every page that sends it there (#42: the check-in
page must log no console error over plain HTTP). Behind HTTPS, or a proxy
that says so (`SECURE_PROXY_SSL_HEADER`), the header stays.
"""

COOP = "Cross-Origin-Opener-Policy"


class PlainHttpCoop:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.is_secure() and COOP in response:
            del response[COOP]
        return response
