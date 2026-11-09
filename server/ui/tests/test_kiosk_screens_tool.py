"""`tools/kiosk_screens.py` (#52, #239) against a stand-in server.

The script checks each page for scrollbars and clipped text once, 2.5 s
after load, in its screenshot contexts. A kiosk page draws some of what it
shows later: now-racing a heat's times, award-presentations the reveal,
standings its rows one by one (each recorded in #239). So with `--video`
the script checks the recording's tab too, every second until it stops,
and fails a page that clips or scrolls at any point of the recording.

The stand-in answers every `kiosk.php` with one page, which clips a line of
text `clip_ms` after it starts. The timing slack of each case:

- The screenshot checks run 2500 ms after load. A clip at 4000 ms leaves
  them 4000 - 2500 = 1500 ms of slack: a check that much late still sees
  nothing, at tv, laptop and phone.
- The recording runs 4000 / 1000 + 2 = 6 s and checks once a second, so a
  clip at 4000 ms has 6000 - 4000 = 2000 ms of slack: the checks at 5 s and
  6 s both see it, and one late tick still leaves the other. That is the
  `+ 2`. The should-pass mutation moves the clip 1500 ms later, inside that
  slack, and the recording still fails the page.
- The 2 s recording stops 4000 - 2000 = 2000 ms before the clip, so it
  passes even with a check 2000 ms late.

A page that reloads, as `kiosk-now-racing.js` does every 5 s with no lane
count, can destroy the page's script context in the middle of a check. The
check then waits for the page to load and tries again, and a page that
never stays loaded is reported unchecked, so a reload never counts as a
pass.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import Error

from tools import kiosk_screens

pytestmark = pytest.mark.browser

CLIPS_AFTER_MS = 4000
LATE_CLIP = """<!doctype html><html><body style="margin:0">
<script>
setTimeout(function () {
  var box = document.createElement('div');
  box.style.cssText = 'width:100px;overflow:hidden;white-space:nowrap';
  box.textContent = 'A racer name far too long for its box';
  document.body.appendChild(box);
}, CLIP_MS);
</script></body></html>"""
RELOADS = """<!doctype html><html><body>
<p>Lane count missing</p>
<script>setTimeout(function () { location.reload(); }, 1000);</script>
</body></html>"""
DESTROYED = (
    "Page.evaluate: Execution context was destroyed, "
    "most likely because of a navigation"
)


@pytest.fixture
def serve():
    servers = []

    def start(html):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def record(base, out, seconds):
    args = ["--out", str(out), "--page", "flag", "--video", "--seconds", str(seconds)]
    return kiosk_screens.main([base, *args])


@pytest.mark.parametrize("clip_ms", [CLIPS_AFTER_MS, CLIPS_AFTER_MS + 1500])
def test_a_page_that_clips_after_the_screenshot_check_fails_in_the_recording(
    serve, tmp_path, capsys, clip_ms
):
    base = serve(LATE_CLIP.replace("CLIP_MS", str(clip_ms)))
    code = record(base, tmp_path, CLIPS_AFTER_MS / 1000 + 2)
    err = capsys.readouterr().err
    # The screenshot checks, at 2.5 s, see nothing yet.
    for size in kiosk_screens.SIZES:
        assert f"flag at {size}" not in err
    assert "flag in the recording" in err
    assert "A racer name" in err
    assert code == 1
    assert (tmp_path / "flag-1920x1080.webm").stat().st_size > 0


def test_a_recording_too_short_to_reach_the_clip_passes(serve, tmp_path, capsys):
    base = serve(LATE_CLIP.replace("CLIP_MS", str(CLIPS_AFTER_MS)))
    code = record(base, tmp_path, 2)
    assert capsys.readouterr().err == ""
    assert code == 0


def test_a_page_that_reloads_every_second_is_checked_to_the_end(
    serve, tmp_path, capsys
):
    code = record(serve(RELOADS), tmp_path, 4)
    assert capsys.readouterr().err == ""
    assert code == 0
    assert (tmp_path / "flag-1920x1080.webm").stat().st_size > 0


class ReloadingTab:
    """A tab whose first `reloads` evaluations a reload interrupts, then
    shows one clipped element."""

    def __init__(self, reloads):
        self.reloads = reloads
        self.loads_waited = 0

    def evaluate(self, script):
        if self.reloads:
            self.reloads -= 1
            raise Error(DESTROYED)
        return False if script == kiosk_screens.SCROLLS else ["<div>clipped</div>"]

    def wait_for_load_state(self, *args, **kwargs):
        self.loads_waited += 1

    def wait_for_timeout(self, ms):
        pass


def test_a_check_a_reload_interrupts_is_tried_again_once_the_page_loads():
    tab = ReloadingTab(reloads=1)
    problems = kiosk_screens.watch(tab, "flag", 1)
    assert problems == ["flag in the recording at 1 s: clipped <div>clipped</div>"]
    assert tab.loads_waited == 1


def test_a_page_that_never_stays_loaded_is_reported_not_passed():
    problems = kiosk_screens.watch(ReloadingTab(reloads=1000), "flag", 1)
    assert problems == [
        "flag in the recording at 1 s: not checked, the page reloaded through "
        f"{kiosk_screens.TRIES} tries"
    ]
