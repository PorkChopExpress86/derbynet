"""`tools/kiosk_screens.py` (#52, #239, #256) against a stand-in server.

The script checks each page for scrollbars, clipped text, text cut short
with an ellipsis and text drawn over another element, once, 2.5 s after
load, in its screenshot contexts. A kiosk page draws some of what it shows
later: now-racing a heat's times, award-presentations the reveal, standings
its rows one by one (each recorded in #239). So with `--video` the script
checks the recording's tab too, every second until it stops, and fails a
page that has a problem at any point of the recording.

The recording tests' stand-in answers every `kiosk.php` with one page,
which clips a line of text and draws a box over a caption `clip_ms` after
it starts. The timing slack of each case:

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

import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error

from tools import kiosk_screens

SERVER_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.browser

CLIPS_AFTER_MS = 4000
LATE_CLIP = """<!doctype html><html><body style="margin:0">
<script>
setTimeout(function () {
  var box = document.createElement('div');
  box.style.cssText = 'width:100px;overflow:hidden;white-space:nowrap';
  box.textContent = 'A racer name far too long for its box';
  document.body.appendChild(box);
  var caption = document.createElement('p');
  caption.id = 'caption';
  caption.style.cssText = 'position:absolute;left:0;top:40px;margin:0';
  caption.textContent = 'A caption under a car';
  document.body.appendChild(caption);
  var car = document.createElement('div');
  car.id = 'car';
  car.style.cssText = 'position:absolute;left:0;top:36px;width:80px;height:30px;'
    + 'background:#c00';
  document.body.appendChild(car);
}, CLIP_MS);
</script></body></html>"""
# The overlap and ellipsis checks (#256) on four stand-in pages, each
# checked once 2.5 s after load at tv, laptop and phone. Every page is laid
# out in px within 390x844, so it shows the same at each size and never
# scrolls.
#
# OVERLAPPING draws text over another element, three ways the check must
# report:
# - two captions printed over each other (#253), one line for the pair;
# - text under a flag that waves for ever: an endless animation never
#   rests, so the check judges it as it moves. The text's box under the
#   flag holds no text of its own, so it gets no line;
# - text under a car whose drive, an animation of a set length, ends
#   PARKS_MS after load and, filled forwards, leaves it over the text. A
#   finished animation that fills its end stays in `getAnimations()`, as
#   kiosk.css's `both` fills do, so the check must tell it from a running
#   one. The check skips the car while it drives (the near misses' car
#   drives for 600 s) and judges it once it rests. The screenshot checks run
#   2500 ms after load, so the car rests 2500 - PARKS_MS = 1500 ms before
#   them, the slack the recording tests' clip gives the same checks: the
#   should-pass mutation parks it at 2400 ms, 100 ms before the checks, and
#   the page still reports it.
PARKS_MS = 1000
OVERLAPPING = """<!doctype html><html><body style="margin:0;font:16px sans-serif">
<style>
  @keyframes wave { 50% { transform: rotate(3deg); } }
  @keyframes drive { to { transform: translateX(1px); } }
</style>
<p id="under-car" style="position:absolute;left:10px;top:150px;margin:0">Parked over</p>
<div id="car" style="position:absolute;left:10px;top:146px;width:60px;height:30px;
  background:#c00;animation:drive PARKS_MSms linear forwards"></div>
<p id="first" style="position:absolute;left:10px;top:10px;margin:0">First caption</p>
<p id="second" style="position:absolute;left:14px;top:14px;margin:0">Second caption</p>
<div id="pole" style="position:absolute;left:0;top:70px;width:200px;height:40px">
  <p id="under-flag" style="position:absolute;left:10px;top:10px;margin:0">
  Text under the flag</p></div>
<div id="flag" style="position:absolute;left:10px;top:76px;width:60px;height:30px;
  background:#c00;animation:wave 4s infinite"></div>
</body></html>"""
# CONFETTI_ELSEWHERE is just outside the confetti's allowance
# (`kiosk_screens.DRAWN_OVER`), three ways the check must report: text
# outside an award over its confetti, another video over an award's text,
# and a confetti video an award holds inside another element. The first
# award's box is empty, so the text outside it overlaps only its confetti.
CONFETTI_ELSEWHERE = """<!doctype html>
<html><body style="margin:0;font:16px sans-serif">
<style>
  .k-award { position: absolute; left: 10px; width: 300px; height: 60px; }
  video { position: absolute; inset: 0; width: 100%; height: 100%; }
</style>
<div class="k-award" style="top:0;width:0;height:0">
  <video id="confetti" data-confetti style="width:300px;height:60px"></video></div>
<p id="not-award" style="position:absolute;left:20px;top:10px;margin:0">Not an award</p>
<div class="k-award" style="top:80px">
  <p id="award" style="margin:0">Fastest in Pack</p>
  <video id="other-video"></video></div>
<div class="k-award" style="top:160px">
  <p id="award-2" style="margin:0">Best in Show</p>
  <div><video id="nested-confetti" data-confetti></video></div></div>
</body></html>"""
# CUT_SHORT cuts a racer's name with "…" (#250), twice: once still, and
# once inside a box that glows for 600 s, as a winning lane's `k-glow`
# does. The overlap check's skip of a moving element does not apply to
# text cut short, which shows however its box moves.
CUT_SHORT = """<!doctype html><html><body style="margin:0;font:16px sans-serif">
<style>
  .cut { width: 100px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
  @keyframes glow { to { box-shadow: 0 0 1px #fc0; } }
</style>
<div id="name" class="cut">A racer name far too long for its box</div>
<div style="animation:glow 600s linear">
  <div id="glowing-name" class="cut">Another name far too long for its box</div></div>
</body></html>"""
# NEAR_MISSES holds a case of each kind the checks must pass, one per band
# (most 60 px tall), so an over-eager check fails the page:
# - text and a box that only touch, side by side;
# - an ancestor's text under its descendant's, and the other way round;
# - text under elements nobody sees: `visibility: hidden`, inside an
#   ancestor with `opacity: 0`, cut away by an ancestor that hides its
#   overflow, and two captions over each other off the screen;
# - text set to end with an ellipsis that fits its box, inline text with
#   the same rule, which text-overflow does not apply to, and hidden text
#   cut short;
# - a table's heading under its `<col>` and `<colgroup>`;
# - two lines of big inline numbers with a tight line-height, as on the
#   scale-MPH page, whose fonts' boxes overlap though their lines do not;
# - a heading in a multi-column list, beside a list split over both
#   columns, as on please-check-in;
# - text under a box that moves for 600 s, as the scale-MPH car drives;
# - an award's text under its confetti, the allowance.
NEAR_MISSES = """<!doctype html><html><body style="margin:0;font:16px sans-serif">
<style>
  .band { position: absolute; left: 0; width: 380px; height: 60px; }
  .ends { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .over { position: absolute; left: 0; top: 0; width: 120px; height: 20px;
    background: #c00; }
  @keyframes drive { to { transform: translateX(1px); } }
</style>
<div class="band" style="top:0;white-space:nowrap"><span>Touching</span><span
  style="display:inline-block;width:40px;height:16px;background:#036"></span></div>
<div class="band" style="top:60px;background:#036;color:#fff">Parent text
  <span style="position:absolute;left:0;top:0;background:#c00">Child</span></div>
<div class="band" style="top:120px">
  <p style="margin:0">Under hidden things</p>
  <div class="over" style="visibility:hidden"></div>
  <div style="opacity:0"><div class="over"></div></div>
  <div style="position:absolute;left:200px;top:0;width:50px;height:20px;
    overflow:hidden">
    <div style="position:relative;left:-200px;width:120px;height:20px;background:#c00">
    </div></div>
</div>
<p style="position:absolute;left:-1000px;top:0;margin:0">Off the screen</p>
<p style="position:absolute;left:-998px;top:2px;margin:0">Also off the screen</p>
<div class="band" style="top:180px">
  <div class="ends" style="width:300px">Fits its box</div>
  <span class="ends">Inline, never cut</span>
  <div class="ends" style="width:40px;visibility:hidden">Hidden and cut short</div>
</div>
<table class="band" style="top:240px;table-layout:fixed;border-spacing:0">
  <colgroup><col style="width:100px"></colgroup>
  <tr><th style="text-align:left">Heat 4</th><td>Lane 1</td></tr></table>
<div class="band" style="top:300px;height:120px;font-size:40px;line-height:1">
  <p style="margin:0">equivalent of <b style="font-size:1.25em">891</b></p>
  <p style="margin:0">took <b style="font-size:1.25em">3.5</b></p></div>
<div class="band" style="top:420px;height:80px;columns:2;column-gap:10px;
  column-fill:auto;font-size:14px">
  <section><h2 style="font-size:14px;margin:0">Bears</h2>
    <ul style="margin:0">
      <li>One</li><li>Two</li><li>Three</li><li>Four</li><li>Five</li><li>Six</li>
    </ul></section>
  <section style="break-inside:avoid"><h2 style="font-size:14px;margin:0">Webelos</h2>
    <ul style="margin:0"><li>Seven</li></ul></section></div>
<div class="band" style="top:500px">
  <p style="margin:0">Under a passing car</p>
  <div class="over" style="animation:drive 600s linear"></div></div>
<div class="band k-award" style="top:560px">
  <p style="margin:0">Fastest in Pack</p>
  <video data-confetti style="position:absolute;inset:0;width:100%;height:100%"></video>
</div>
</body></html>"""
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
        """Serves `html`, or with a dict of kiosk page names, each page's
        own HTML."""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                page = parse_qs(urlsplit(self.path).query).get("page", [""])[0]
                name = page.removeprefix("kiosks/").removesuffix(".kiosk")
                body = (html[name] if isinstance(html, dict) else html).encode()
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
    """Records the flag page for `seconds`. Returns the exit code and
    stderr."""
    return run(base, out, "--page", "flag", "--video", "--seconds", str(seconds))


def run(base, out, *args):
    """Runs the script as its own process, as it is run for real. In this
    process, a worker that already ran a browser test holds Playwright's
    event loop, and the script's `sync_playwright()` refuses to start
    (#239, CI run 42540562729). Returns the exit code and stderr."""
    args = ["--out", str(out), *args]
    result = subprocess.run(
        [sys.executable, "tools/kiosk_screens.py", base, *args],
        cwd=SERVER_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stderr


@pytest.mark.parametrize("clip_ms", [CLIPS_AFTER_MS, CLIPS_AFTER_MS + 1500])
def test_a_page_that_clips_after_the_screenshot_check_fails_in_the_recording(
    serve, tmp_path, clip_ms
):
    base = serve(LATE_CLIP.replace("CLIP_MS", str(clip_ms)))
    code, err = record(base, tmp_path, CLIPS_AFTER_MS / 1000 + 2)
    # The screenshot checks, at 2.5 s, see nothing yet.
    for size in kiosk_screens.SIZES:
        assert f"flag at {size}" not in err
    assert "flag in the recording" in err
    assert "A racer name" in err
    late = (
        r'^flag in the recording at \d+ s: overlaps <p id="caption".* and <div id="car'
    )
    assert re.search(late, err, re.M)
    assert code == 1
    assert (tmp_path / "flag-1920x1080.webm").stat().st_size > 0
    assert (tmp_path / "problems.txt").read_text() == err


def test_a_recording_too_short_to_reach_the_clip_passes(serve, tmp_path):
    base = serve(LATE_CLIP.replace("CLIP_MS", str(CLIPS_AFTER_MS)))
    code, err = record(base, tmp_path, 2)
    assert err == ""
    assert code == 0
    assert (tmp_path / "problems.txt").read_text() == "no problems\n"


def test_a_page_that_reloads_every_second_is_checked_to_the_end(serve, tmp_path):
    code, err = record(serve(RELOADS), tmp_path, 4)
    assert err == ""
    assert code == 0
    assert (tmp_path / "flag-1920x1080.webm").stat().st_size > 0


PAGES = {
    "flag": OVERLAPPING.replace("PARKS_MS", str(PARKS_MS)),
    "identify": CONFETTI_ELSEWHERE,
    "welcome": CUT_SHORT,
    "derbynet": NEAR_MISSES,
}


def test_overlapping_text_and_text_cut_with_an_ellipsis_fail_their_pages(
    serve, tmp_path
):
    """Each failing stand-in page reports its problems at every size, in the
    tool's own lines, and the page of near misses reports none (#256)."""
    pages = [arg for name in PAGES for arg in ("--page", name)]
    code, err = run(serve(PAGES), tmp_path, *pages)
    found = sorted(
        re.findall(
            r'^(\S+) at (\S+): (overlaps|cut with an ellipsis) <\w+ id="([\w-]+)"'
            r'(?:.* and <\w+ id="([\w-]+)")?',
            err,
            re.MULTILINE,
        )
    )
    expected = [
        ("flag", "overlaps", "first", "second"),
        ("flag", "overlaps", "under-flag", "flag"),
        ("flag", "overlaps", "under-car", "car"),
        ("identify", "overlaps", "not-award", "confetti"),
        ("identify", "overlaps", "award", "other-video"),
        ("identify", "overlaps", "award-2", "nested-confetti"),
        ("welcome", "cut with an ellipsis", "name", ""),
        ("welcome", "cut with an ellipsis", "glowing-name", ""),
    ]
    assert found == sorted(
        (page, size, kind, first, second)
        for page, kind, first, second in expected
        for size in kiosk_screens.SIZES
    )
    assert len(err.splitlines()) == len(found)
    assert code == 1
    assert (tmp_path / "problems.txt").read_text() == err


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
        if script == kiosk_screens.SCROLLS:
            return False
        return ["<div>clipped</div>"] if script == kiosk_screens.CLIPPED else []

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
