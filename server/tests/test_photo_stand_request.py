"""The photo stand's upload request carries its file and form fields that
fit in `PHOTO_STAND_FORM_BYTES`, which the upload health check adds to the
photo stand's file before comparing nginx's cap (#48, D134). The request is
the photo stand's own curl command (`upload_photo` in
`extras/scripts/lib/photo-functions.sh`), sent to a local server that counts
its body."""

import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from core.diagnostics import PHOTO_STAND_FORM_BYTES

SCRIPT = Path(__file__).resolve().parents[2] / "extras/scripts/lib/photo-functions.sh"


def _form_fields() -> list[str]:
    """The `-F` values of `upload_photo`'s curl command, as the script
    writes them."""
    text = SCRIPT.read_text()
    start = text.index("curl --location", text.index("upload_photo()"))
    end = text.index('"$DERBYNET_SERVER/action.php"', start)
    words = shlex.split(text[start:end].replace("\\\n", " "))
    fields = [words[i + 1] for i, word in enumerate(words) if word == "-F"]
    assert any(field.startswith("photo=@$PHOTO_PATH") for field in fields)
    return fields


def _body_length(fields: list[str]) -> int:
    lengths = []

    class Counter(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            self.rfile.read(n)
            lengths.append(n)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Counter)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        args = [arg for field in fields for arg in ("-F", field)]
        url = f"http://127.0.0.1:{server.server_port}/action.php"
        subprocess.run(["curl", "-s", "-o", "/dev/null", *args, url], check=True)
    finally:
        thread.join(timeout=10)
        server.server_close()
    return lengths[0]


@pytest.mark.parametrize(
    ("repo", "barcode"), [("car", "PWD101"), ("head", "PWD999999")]
)
def test_photo_stand_form_fields_fit(tmp_path, repo, barcode):
    photo = tmp_path / f"Car{barcode.removeprefix('PWD')}.jpg"
    photo.write_bytes(b"\xff" * 1000)
    values = {
        "$PHOTO_REPO": repo,
        "$BARCODE": barcode,
        "$AUTOCROP": "0",
        "$PHOTO_PATH": str(photo),
    }
    fields = []
    for field in _form_fields():
        for name, value in values.items():
            field = field.replace(name, value)
        assert "$" not in field
        fields.append(field)
    overhead = _body_length(fields) - photo.stat().st_size
    assert 0 < overhead <= PHOTO_STAND_FORM_BYTES
