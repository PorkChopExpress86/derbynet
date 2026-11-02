"""The URLs a phone or a kiosk can reach this server at, ported from
`preferred_urls()` in `website/inc/locked.inc` (#40).

#48 lists them on the about page, #46 makes the first one the default
`qrcode` kiosk content, and #42 uses the `https` form for the mobile check-in
QR code.
"""

import ipaddress
import socket

from django.conf import settings
from django.http.request import split_domain_port
from django.urls import get_script_prefix


def _config_url() -> str | None:
    """The whole URL from `<config dir>/config-url`, such as
    `http://my-host.com/with/a/long/path`, when the file exists."""
    try:
        return (settings.CONFIG_DIR / "config-url").read_text().strip()
    except FileNotFoundError:
        return None


def _host_addresses() -> list[str]:
    """The IPv4 addresses the host's name resolves to, leaving out loopback,
    which no other device can reach. PHP lists them all."""
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    unique = dict.fromkeys(addresses)
    return [a for a in unique if not ipaddress.ip_address(a).is_loopback]


def _behind_proxy(request) -> bool:
    headers = request.headers
    return "x-forwarded-for" in headers or "x-forwarded-proto" in headers


def preferred_urls(request, use_https: bool = False) -> list[str]:
    """The server's URLs, best first, without a trailing slash.

    `config-url` wins when it exists. Otherwise each host address makes a URL,
    with the request's port and the server's URL prefix. Behind a proxy the
    `Host` header names the server instead, as PHP reads `HTTP_HOST` when
    `X-Forwarded-For` is set. A host with no address other than loopback also
    falls back to `Host`. `use_https` forces the `https` scheme.
    """
    if (configured := _config_url()) is not None:
        return [configured]
    scheme = "https" if use_https else request.scheme
    prefix = get_script_prefix().rstrip("/")
    if _behind_proxy(request) or not (addresses := _host_addresses()):
        return [f"{scheme}://{request.get_host()}{prefix}"]
    # The port the client used, from Host. Forcing https assumes a proxy on 443.
    _, port = split_domain_port(request.get_host())
    suffix = f":{port}" if port and not use_https else ""
    return [f"{scheme}://{address}{suffix}{prefix}" for address in addresses]
