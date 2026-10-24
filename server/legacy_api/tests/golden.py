"""Read the PHP transcripts under server/tests/golden/http and replay them.

The format is the one `capture/transcript.mjs` documents.
"""

from dataclasses import dataclass, field
from pathlib import Path

HTTP_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden" / "http"

_KEYS = {
    "request": "request",
    "user-agent": "user_agent",
    "request-body": "request_body",
    "status": "status",
    "content-type": "content_type",
    "location": "location",
    "cache-control": "cache_control",
}


@dataclass
class Exchange:
    number: str = ""
    name: str = ""
    request: str = ""
    user_agent: str | None = None
    request_body: str | None = None
    status: int = 0
    content_type: str | None = None
    location: str | None = None
    cache_control: str | None = None
    set_cookies: list[str] = field(default_factory=list)
    body: bytes = b""


def transcript(name: str) -> list[Exchange]:
    data = (HTTP_DIR / f"{name}.http").read_bytes()
    exchanges, pos = [], 0
    while pos < len(data):
        ex = Exchange()
        while True:
            eol = data.index(b"\n", pos)
            line = data[pos:eol].decode()
            pos = eol + 1
            if line == "body:":
                break
            key, _, value = line.partition(" ")
            key = key.removesuffix(":")
            if key == "###":
                ex.number, ex.name = value.split(" ", 1)
            elif key == "length":
                length = int(value)
            elif key == "set-cookie":
                ex.set_cookies.append(value)
            elif key in _KEYS:
                setattr(ex, _KEYS[key], int(value) if key == "status" else value)
        ex.body = data[pos : pos + length]
        pos += length + 1
        exchanges.append(ex)
    return exchanges


def exchange(transcript_name: str, name: str) -> Exchange:
    [found] = [ex for ex in transcript(transcript_name) if ex.name == name]
    return found


def replay(client, ex: Exchange):
    """Send the exchange's request to Django, at the root path."""
    method, target = ex.request.split(" ", 1)
    path = "/" + target
    if method == "GET":
        return client.get(path)
    return client.post(
        path, ex.request_body or "", content_type="application/x-www-form-urlencoded"
    )
