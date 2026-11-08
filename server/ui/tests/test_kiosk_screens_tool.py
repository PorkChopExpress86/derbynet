"""`tools/kiosk_screens.py` (#52, #239) against a stand-in server.

The script checks each page for scrollbars and clipped text once, 2.5 s
after load, in its screenshot contexts. A kiosk page draws most of what it
shows later: a heat's times, an award's reveal, the standings rows one by
one. So with `--video` the script checks the recording's tab too, every
second until it stops, and fails a page that clips or scrolls at any point
of the recording (#239).

The stand-in answers every `kiosk.php` with one page that clips a line of
text only after `CLIPS_AFTER_MS`, past the screenshot check's 2.5 s and
inside the recording's `--seconds`.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import kiosk_screens

pytestmark = pytest.mark.browser

CLIPS_AFTER_MS = 4000
LATE_CLIP = f"""<!doctype html><html><body style="margin:0">
<script>
setTimeout(function () {{
  var box = document.createElement('div');
  box.style.cssText = 'width:100px;overflow:hidden;white-space:nowrap';
  box.textContent = 'A racer name far too long for its box';
  document.body.appendChild(box);
}}, {CLIPS_AFTER_MS});
</script></body></html>"""


@pytest.fixture
def stand_in():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = LATE_CLIP.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def record(base, out, seconds):
    args = ["--out", str(out), "--page", "flag", "--video", "--seconds", str(seconds)]
    return kiosk_screens.main([base, *args])


def test_a_page_that_clips_after_the_screenshot_check_fails_in_the_recording(
    stand_in, tmp_path, capsys
):
    code = record(stand_in, tmp_path, CLIPS_AFTER_MS / 1000 + 2)
    err = capsys.readouterr().err
    # The screenshot check, at 2.5 s, sees nothing yet.
    assert "flag at tv" not in err
    assert "flag in the recording" in err
    assert "A racer name" in err
    assert code == 1
    assert (tmp_path / "flag-1920x1080.webm").stat().st_size > 0


def test_a_recording_too_short_to_reach_the_clip_passes(stand_in, tmp_path, capsys):
    code = record(stand_in, tmp_path, 2)
    assert capsys.readouterr().err == ""
    assert code == 0
