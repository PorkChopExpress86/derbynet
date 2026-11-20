"""The Playwright harness for every M4 page (#39, #40).

Each test that takes `page` runs twice, on a phone (390x844, touch) and on a
desktop (1280x800), against pytest-django's live server, which serves the
static files. A test that runs at one size is marked
`@pytest.mark.sizes("desktop", reason=...)`, and collection deselects its
other size before any page opens (#366); a `pytest.skip` on `page.size` in
its body fails `ui/tests/test_browser_size_skips.py`. Run them, after
installing Chromium once:

    uv run playwright install chromium
    uv run pytest -m browser ui

Every page fails its test on a page error, a console error, or a failed
request for a static file, unless the test expects that error with
`page_errors.allow(pattern)`. `shoot(page, name)` saves a screenshot under
`DERBYNET_SCREENSHOT_DIR` (default `server/var/screenshots`), which CI
uploads, and `assert_accessible(page)` runs axe-core and fails on a serious or
critical violation. `no_sideways_scroll(page)` fails a page wider than its
viewport; each `shoot()` runs it, and so does the end of every test (#44
retro: a phone page scrolled 407 px sideways, seen only in a screenshot).
A click, tap or check on a control a toast overlaps, or comes within
48 px of, fails at once (`TOAST_OVER`); close the toasts with
`dismiss_toasts(page)` first.

After a control posts, wrap the click in `with htmx_post(page, path):` before
reading the database: it waits for the POST's reply and htmx's swap. When the
test body returns, the harness stops every tab's timers, closes the other
tabs, stops the page's fetch and XMLHttpRequest requests, blocks every
other request the page sends after that, and waits for every request still
in flight before any fixture tears down (#45 and
#53 retros), so a late reply or a late poll never meets a flushed database
or `settings` already put back. Close a second tab with
`close_tab(page, tab)`: a tab closed with a request out fails the test.
`wait_for_load_state("networkidle")` waits for a page load only, and returns
at once after an htmx request. A swap that leaves an `hx-get` or `hx-post`
control unbound once its task ends fails the test (#46 retro).
"""

import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
import pytest_timeout

pytest.importorskip("playwright", reason="install the browser dependency group")

from importlib import import_module  # noqa: E402

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402

from accounts.models import Role  # noqa: E402
from awards.models import AwardType  # noqa: E402
from core.tests.factories import EventFactory  # noqa: E402
from ui.tests.browser.helpers import dismiss_toasts, htmx_settled  # noqa: E402, F401
from ui.tests.roles import PASSWORDS  # noqa: E402

SIZES = {
    "phone": {
        "viewport": {"width": 390, "height": 844},
        "has_touch": True,
        "is_mobile": True,
        "device_scale_factor": 2,
    },
    "desktop": {"viewport": {"width": 1280, "height": 800}},
}
# A swap must bind its content's hx-* attributes before the browser can run
# anything else, or a tap on a control just shown sends nothing (#45's award
# sheet in CI, #46 retro). htmx binds at settle, so the base template settles
# at once; this fails any test whose swap leaves a control unbound after the
# task that swapped it, whatever the timing of the test's own steps.
UNBOUND_AFTER_SWAP = """document.addEventListener('htmx:afterSwap', (event) => {
  const root = event.detail.target;
  setTimeout(() => {
    if (!root.isConnected) return;
    const unbound = [root, ...root.querySelectorAll('[hx-get], [hx-post]')].filter(
      (e) => e.matches('[hx-get], [hx-post]') && e.isConnected
        && !(e['htmx-internal-data'] || {}).initHash);
    if (unbound.length) {
      const asked = event.detail.requestConfig || {};
      const tag = unbound[0].outerHTML.slice(0, 120);
      console.error(`htmx left unbound after ${asked.verb} ${asked.path}: ${tag}`);
    }
  }, 0);
});"""
AXE = Path(__file__).parent / "vendor" / "axe-4.10.3.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
SCREENSHOTS = Path(
    os.environ.get("DERBYNET_SCREENSHOT_DIR")
    or settings.BASE_DIR / "var" / "screenshots"
)


# pytest 9.1 bound a conftest's fixtures to the first collector of its
# folder, so a browser file named on the command line after a file outside
# browser/ lost every fixture here, and each of its tests failed at setup
# with `SynchronousOnlyOperation` (pytest-dev/pytest#14635, #282). A pytest
# that drops them again fails the run at collection instead.
HARNESS_LOST = (
    "{} lost the browser harness's fixtures; pytest-dev/pytest#14635 drops a "
    "conftest's fixtures when a file outside its folder is named between two "
    "of its files: use pytest 9.2 or later, or name the browser files together"
)


def check_harness(items, here):
    """Raises `HARNESS_LOST` for the first test under `here` that lacks
    `_async_unsafe`, which every test there has as an autouse fixture."""
    for item in items:
        if here in Path(item.fspath).parents and "_async_unsafe" not in (
            item.fixturenames
        ):
            raise pytest.UsageError(HARNESS_LOST.format(item.nodeid))


def split_by_size(items) -> tuple[list, list]:
    """`(kept, dropped)`: a test marked `@pytest.mark.sizes(*names,
    reason=...)` keeps its cases at those `SIZES` and drops the others, so a
    one-size test never opens a page at the other size (#366). Raises
    `pytest.UsageError` for a mark that names no size, a name not in `SIZES`,
    no reason, or a test that takes no `size`."""
    kept, dropped = [], []
    for item in items:
        mark = item.get_closest_marker("sizes")
        if mark is None:
            kept.append(item)
            continue
        if not mark.args or not set(mark.args) <= set(SIZES):
            raise pytest.UsageError(
                f"{item.nodeid}: sizes() takes names from {tuple(SIZES)}, "
                f"not {mark.args}"
            )
        if not mark.kwargs.get("reason"):
            raise pytest.UsageError(f"{item.nodeid}: sizes() needs a reason=")
        callspec = getattr(item, "callspec", None)
        if callspec is None or "size" not in callspec.params:
            raise pytest.UsageError(f"{item.nodeid}: sizes() on a test with no page")
        (kept if callspec.params["size"] in mark.args else dropped).append(item)
    return kept, dropped


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    browser = [item for item in items if here in Path(item.fspath).parents]
    for item in browser:
        item.add_marker(pytest.mark.browser)
    check_harness(items, here)
    dropped = split_by_size(browser)[1]
    if dropped:
        dropped[0].config.hook.pytest_deselected(items=dropped)
        gone = {id(item) for item in dropped}
        items[:] = [item for item in items if id(item) not in gone]


# A host name that reaches the live server over plain HTTP. `localhost` is a
# secure context, so a page there may use the camera; check-in's photo sheet
# must also work where the browser refuses it, as on a venue's LAN (#42).
PLAIN_HTTP_HOST = "derbynet.test"


# Puts a family first for fontconfig's `sans-serif` (`font_config`).
FONTS_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <match target="pattern">
    <test name="family"><string>sans-serif</string></test>
    <edit name="family" mode="prepend" binding="strong"><string>{family}</string></edit>
  </match>
</fontconfig>
"""


def font_config(family: str, folder: Path) -> dict[str, str]:
    """The environment that makes Chromium draw `system-ui`, which
    kiosk.css's stack starts with, in `family`: a fontconfig file that puts
    it first for fontconfig's `sans-serif`, which Chromium asks for
    `system-ui` (`test_a_forced_font_draws_the_kiosk`). A CSS `sans-serif`
    goes to Chromium's own default, Arial, and stays. It fails when
    `family` is not installed, since fontconfig would fall back silently."""
    found = subprocess.run(
        ["fc-match", "-f", "%{family}", family],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if family not in found.split(","):
        raise pytest.UsageError(f"font {family!r} is not installed: {found!r}")
    conf = folder / "fonts.conf"
    conf.write_text(FONTS_CONF.format(family=escape(family)))
    return {**os.environ, "FONTCONFIG_FILE": str(conf)}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, tmp_path_factory):
    """Chromium resolves `PLAIN_HTTP_HOST` to the live server, and offers
    two fake cameras that need no permission prompt, so the replay setup can
    choose between them (#53). `DERBYNET_BROWSER_FONT="DejaVu Sans"` draws
    `system-ui` as Ubuntu 24.04 does (`font_config`, #273 retro)."""
    family = os.environ.get("DERBYNET_BROWSER_FONT")
    if family:
        folder = tmp_path_factory.mktemp("fonts")
        browser_type_launch_args = {
            **browser_type_launch_args,
            "env": font_config(family, folder),
        }
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            f"--host-resolver-rules=MAP {PLAIN_HTTP_HOST} 127.0.0.1",
            "--use-fake-device-for-media-stream=device-count=2",
            "--use-fake-ui-for-media-stream",
        ],
    }


@pytest.fixture(scope="session", autouse=True)
def _async_unsafe():
    """Playwright's sync API runs an event loop in the test thread, and Django
    refuses ORM calls from a thread with a running loop. Session scope sets it
    before pytest-django opens the database, and only once a browser test
    runs, so a default run keeps the check."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def _css_is_fresh(tmp_path_factory, config) -> bool:
    """`css_build.check()`, run once per pytest run. Tailwind CLIs started at
    once by several xdist workers can fail to load their native binding, so
    the first worker runs it under a file lock in the directory every worker
    shares, and the others read its answer. It reads `config.workerinput`
    rather than xdist's `worker_id` fixture, which `-p no:xdist` removes: that
    errored every browser test at setup in #220 (#220 retro)."""
    from ui import css_build

    if not hasattr(config, "workerinput"):
        return css_build.check()
    import fcntl

    shared = tmp_path_factory.getbasetemp().parent
    answer = shared / "css-fresh.txt"
    with (shared / "css-fresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not answer.exists():
            answer.write_text(str(css_build.check()))
        return answer.read_text() == "True"


@pytest.fixture(scope="session", autouse=True)
def _fresh_css(tmp_path_factory, pytestconfig):
    """A page styled by a stale `app.css` lacks the classes a template just
    gained, so its screenshots and layout checks mislead. Where the Tailwind
    CLI is installed, refuse to run on a stale build; the `ui` CI job checks
    the same before it runs these tests."""
    from ui import css_build

    if css_build.binary_path().exists() and not _css_is_fresh(
        tmp_path_factory, pytestconfig
    ):
        pytest.fail(css_build.OUT_OF_DATE.format(css_build.OUTPUT), pytrace=False)


# The text of the first toast within `margin` px of the element, or null. The
# toasts stay 8 s, or until closed for an error, and Playwright clicks the
# centre of what it clicks. A toast over the bottom of a button let a local
# click through and timed out on CI's fonts (#212 retro), so any overlap
# fails, the same on every machine.
TOAST_OVER = """(element, margin) => {
  if (element.closest('#toasts')) return null;
  const r = element.getBoundingClientRect();
  const box = {left: r.left, right: r.right, top: r.top - margin,
               bottom: r.bottom + margin};
  for (const toast of document.querySelectorAll('#toasts > *')) {
    const t = toast.getBoundingClientRect();
    if (t.width && t.height && box.left < t.right && t.left < box.right
        && box.top < t.bottom && t.top < box.bottom) {
      return toast.textContent.trim().replace(/\\s+/g, ' ').slice(0, 80);
    }
  }
  return null;
}"""
# CI's fonts move the page and the toasts: two clicks clear locally by up
# to 32 px were covered there (PR #223). So the target counts as covered
# within this margin above and below it.
TOAST_MARGIN = 48
CLEAR_OF_TOASTS = ("click", "dblclick", "tap", "check", "uncheck")


def _clear_of_toasts(action):
    def act(self, *args, **kwargs):
        if not kwargs.get("force") and not kwargs.get("trial"):
            from playwright.sync_api import Error

            try:
                # Where the click lands: Playwright scrolls the element in first.
                self.scroll_into_view_if_needed(timeout=kwargs.get("timeout"))
                toast = self.evaluate(TOAST_OVER, TOAST_MARGIN)
            except Error:
                # A swap replaced the element mid-check (seen once on the
                # photos page); the action finds the new one, and its own
                # wait and error stand.
                toast = None
            if toast is not None:
                raise AssertionError(
                    f"the toast {toast!r} covers {self}: dismiss_toasts(page) "
                    "first, as a race crew would"
                )
        return action(self, *args, **kwargs)

    return act


@pytest.fixture(scope="session", autouse=True)
def _toasts_cover_nothing_clicked():
    """Each click, tap and check fails at once when a toast overlaps its
    target, where Playwright waited 30 s for the toast to go."""
    from playwright.sync_api import Locator

    with pytest.MonkeyPatch.context() as patch:
        for name in CLEAR_OF_TOASTS:
            patch.setattr(Locator, name, _clear_of_toasts(getattr(Locator, name)))
        yield


@pytest.fixture(autouse=True)
def _database(transactional_db, live_server):
    """The live server reads what the test writes, so the test runs outside a
    transaction. The flush after each test removes the roles the migration
    seeded, so seed them again, with the migration's own function."""
    if not Role.objects.exists():
        import_module("accounts.migrations.0002_seed_default_roles").seed(apps, None)
    if not AwardType.objects.exists():
        seed = import_module("awards.migrations.0003_seed_award_types")
        seed.seed_award_types(apps, SimpleNamespace(connection=connection))


@pytest.fixture
def event(_database):
    return EventFactory()


@pytest.fixture(params=list(SIZES))
def size(request) -> str:
    return request.param


class PageErrors:
    """What went wrong while a page loaded or ran.

    htmx logs an aborted request (an `hx-sync` replace, or a poll cut short)
    as two console errors, `htmx:afterRequest` then `htmx:sendAbort`. An abort
    is no failure, so that pair is dropped here and counted in `aborts`; the
    browser's `net::ERR_ABORTED` for the request still needs the test's
    `allow()`. A lone `htmx:afterRequest` is a failed request and stays."""

    # Chromium's console error for a page or resource answered 404; its
    # text names no URL, which the message's location holds.
    MISSING = "Failed to load resource: the server responded with a status of 404"

    def __init__(self):
        self.errors: list[str] = []
        self.allowed: list[re.Pattern] = []
        self.allowed_missing: list[re.Pattern] = []
        self.aborts = 0
        self._after_request = False

    def allow(self, pattern: str):
        self.allowed.append(re.compile(pattern))

    def allow_missing(self, url_pattern: str):
        """Drop the 404 console error of a URL `url_pattern` matches, and
        only that one: a page meant to answer 404 (#206's bad vote link)."""
        self.allowed_missing.append(re.compile(url_pattern))

    def add(self, message: str):
        if not any(p.search(message) for p in self.allowed):
            self.errors.append(message)

    def console_error(self, text: str, url: str = ""):
        if text.startswith(self.MISSING) and any(
            p.search(url) for p in self.allowed_missing
        ):
            return
        after_request, self._after_request = self._after_request, False
        if text == "htmx:sendAbort":
            self.aborts += 1
            return
        if after_request:
            self.add("console: htmx:afterRequest")
        if text == "htmx:afterRequest":
            self._after_request = True
        else:
            self.add(f"console: {text}")

    def final(self) -> list[str]:
        if self._after_request:
            self._after_request = False
            self.add("console: htmx:afterRequest")
        return self.errors


@pytest.fixture
def page_errors() -> PageErrors:
    return PageErrors()


class InFlight:
    """The requests a browser context has sent and not yet finished.

    A navigation ends the old document's htmx and fetch requests and its
    image, script and stylesheet loads, but Playwright then reports neither
    `requestfinished` nor `requestfailed` for them (seen on a reload right
    after a click, #188, for a thumbnail left loading by a link, #47, and
    for the timer's scripts when the Web Serial guard leaves `/timer.php`,
    #50). So when a frame
    navigates, those requests leave the set: their replies can no longer
    reach the page. The new document sends none before it commits."""

    TIMEOUT_MS = 10_000
    ORPHANED = ("xhr", "fetch", "image", "script", "stylesheet")

    def __init__(self, context):
        self.requests = set()
        # Requests a tab still had out when it closed, as "METHOD URL".
        self.closed_with = []
        context.on("request", lambda r: self.requests.add(r))
        context.on("requestfinished", lambda r: self.requests.discard(r))
        context.on("requestfailed", lambda r: self.requests.discard(r))
        context.on("page", self.watch)

    def watch(self, page):
        page.on("framenavigated", lambda frame: self.navigated(frame))
        page.on("close", lambda closed: self.page_closed(closed))

    def of(self, tab) -> set:
        """The requests in flight that `tab` sent."""
        return {r for r in self.requests if _sent_by(r) == tab}

    def page_closed(self, tab):
        """Playwright reports no end for the requests of a tab closed with
        them out (the camera tab's POST, PR #205's ui (postgres)), so they
        leave the set and the harness names them: close a tab with
        `close_tab()`, which waits for them first."""
        left = self.of(tab)
        self.requests -= left
        self.closed_with += sorted(f"{r.method} {r.url}" for r in left)

    def navigated(self, frame):
        self.requests -= {
            r
            for r in self.requests
            if _frame_of(r) == frame and r.resource_type in self.ORPHANED
        }

    def drain(self, page) -> list[str]:
        """Wait until nothing is in flight; return what still is after the
        timeout. Playwright delivers the events only inside its own calls,
        hence the short waits."""
        waited = 0
        while self.requests and waited < self.TIMEOUT_MS and not page.is_closed():
            page.wait_for_timeout(50)
            waited += 50
        return sorted(f"{r.method} {r.url}" for r in self.requests)


def _frame_of(request):
    """The frame that sent `request`, or None where Playwright refuses it: a
    service worker's request, and a `target="_blank"` link's navigation
    request, sent before Playwright has its popup's page
    (`test_a_blank_link_followed_while_a_frame_navigates_is_tracked`, #378)."""
    from playwright.sync_api import Error

    try:
        return request.frame
    except Error:
        return None


def _sent_by(request):
    frame = _frame_of(request)
    return frame.page if frame is not None else None


# Clears every timeout and interval the frame has set. Timer ids count up
# from 1, or from 1e12 under Playwright's clock (`page.clock.install()`), so
# the loop runs over the first and the last SPAN ids from there: from 0 it
# spun the renderer for hours on test_toasts.py's fake clock (#53 retro), and
# over ids 0 to SPAN the fake `clearTimeout` built an Error for each and took
# seconds (#347). Under the fake clock only its own ids are cleared: the
# native ones include the clock's own timer. `window.__pwClock` is the
# global Playwright 1.64's clock sets, private to Playwright and no API. A
# native timer set before `page.clock.install()` keeps running; in the
# settle, STOP_SENDING stops its fetches
# (`test_the_settle_stops_a_poll_set_before_the_fake_clock`). The fake
# `clearTimeout` throws on a pending animation frame's id, which shares the
# clock's ids, so each clear is caught on its own (#347).
STOP_TIMERS = """() => {
  const SPAN = 100000;
  const first = window.__pwClock ? 1e12 : 0;
  // clearTimeout clears an interval too.
  const stop = (id) => {
    try { clearTimeout(id); } catch {}
  };
  const last = setTimeout(() => {});
  for (let id = first; id <= Math.min(last, first + SPAN); id++) stop(id);
  for (let id = Math.max(first + SPAN + 1, last - SPAN); id <= last; id++) stop(id);
}"""

# Makes every later `fetch` a promise that never settles and every later
# XMLHttpRequest send nothing, so no callback of the page sends again: with
# a URL, it first fetches that URL, in the same task (as #424 has it). A
# reply handled after the stop that queues htmx's next request or chains a
# fetch, an animation-frame loop, a native timer under the fake clock then
# starts no request, and no handler of theirs runs on a failure
# (`test_a_reply_handled_after_the_stop_starts_no_request`).
STOP_SENDING = """(barrier) => {
  if (barrier) fetch(barrier).catch(() => {});
  window.fetch = () => new Promise(() => {});
  XMLHttpRequest.prototype.send = function () {};
}"""


def stop_timers(tab):
    """Stop every poll in every frame of `tab`, so no request starts after
    the harness drained them."""
    for frame in tab.frames:
        try:
            frame.evaluate(STOP_TIMERS)
        except Exception:  # noqa: BLE001, S110 - a detached frame has no timers
            pass


def stop_sending(tab):
    """Stop every frame of `tab` but the main frame sending fetch and
    XMLHttpRequest requests; the main frame's stop sends the barrier
    (`_barrier`)."""
    for frame in tab.frames:
        if frame == tab.main_frame:
            continue
        try:
            frame.evaluate(STOP_SENDING, None)
        except Exception:  # noqa: BLE001, S110 - a detached frame sends nothing
            pass


def close_tab(page, tab):
    """Close a second tab of `page`'s context, such as the camera or a
    kiosk, once its polls have stopped and its requests are answered. A tab
    closed with a request out fails the test (`InFlight.page_closed`)."""
    stop_timers(tab)
    waited = 0
    while waited < InFlight.TIMEOUT_MS and page.in_flight.of(tab):
        tab.wait_for_timeout(50)
        waited += 50
    tab.close()


# A fetch the settle sends after it stops the timers.
SETTLE_BARRIER = "/settle-barrier"


def _intercepted(page) -> bool:
    """Whether Playwright already intercepts `page`'s requests: a route on
    the page or on its context turns it on (Playwright 1.64's
    `needsRequestInterception`). Read from Playwright's own route lists,
    which are private, with no default: a release that renames them fails
    here instead of guessing."""
    return bool(page._impl_obj._routes or page.context._impl_obj._routes)


def _barrier(page):
    """Wait until Playwright has reported the requests `page` sent before
    its timers stopped. A `request` event can reach Python after the
    evaluate that sent it returns, so the drain found the set empty with a
    poll's last request out (#224 retro, 1 run in 50 under load;
    `test_the_settle_drains_a_request_sent_as_it_stops`). The barrier is a
    fetch sent after the stop, and its end must reach Python by the path
    the page's own requests take
    (`test_the_barrier_follows_a_fetch_sent_before_it`): through Chromium's
    Fetch domain where a route already has Playwright intercepting, so a
    route answers it there. Elsewhere a URL block on a CDP session of its
    own fails it, and the session stays open until the context closes. A
    route there turned the interception on and off at each settle, and a
    document that committed as it turned on sent its requests, the
    barrier's among them, to the server with no `request` event (#347,
    gap 1: `test_the_settle_sees_every_request_of_a_page_that_commits_as_it_starts`).
    So its end follows
    the earlier fetch's start (the same test). STOP_SENDING sends it, and
    then stops the main frame's fetch and XMLHttpRequest in the same task."""
    if not page.url.startswith("http"):
        return
    if _intercepted(page):
        page.route(f"**{SETTLE_BARRIER}", lambda route: route.fulfill(status=204))
        end = "requestfinished"
    else:
        session = page.context.new_cdp_session(page)
        session.send("Network.enable")
        session.send("Network.setBlockedURLs", {"urls": [f"*{SETTLE_BARRIER}"]})
        end = "requestfailed"
    with page.expect_event(
        end,
        lambda r: r.url.endswith(SETTLE_BARRIER),
        timeout=InFlight.TIMEOUT_MS,
    ):
        page.barrier_sent = True
        page.evaluate(STOP_SENDING, SETTLE_BARRIER)


# Playwright's failure texts for a request `block_sending` blocked: by its
# URL block, and a navigation's by its Fetch block.
BLOCKED = ("inspector", "net::ERR_BLOCKED_BY_CLIENT")


def block_sending(page):
    """Make Chromium block every later request of `page`, for the rest of
    its life: an image, a beacon, a frame made after STOP_SENDING, and a
    navigation, whose own request only the Fetch block stops
    (`test_the_settle_blocks_what_the_stops_leave`). The block ends with its
    CDP session (#409, PR #424), so the session stays open on the page and
    closes with its context. A blocked request fails at once, so every new
    document and frame gets STOP_SENDING first: a fetch of theirs never
    fails into a handler that would send again. A page that loads an
    image again on its error would spin, the block failing each load at
    once; no script under `ui/static/ui/` sets an image's `onerror`."""
    page.add_init_script(f"({STOP_SENDING})(null)")
    blocker = page.context.new_cdp_session(page)
    blocker.send("Network.enable")
    blocker.send("Network.setBlockedURLs", {"urls": ["*"]})
    blocker.on(
        "Fetch.requestPaused",
        lambda paused: blocker.send(
            "Fetch.failRequest",
            {"requestId": paused["requestId"], "errorReason": "BlockedByClient"},
        ),
    )
    blocker.send("Fetch.enable", {"patterns": [{"resourceType": "Document"}]})
    page.blocker = blocker


def _settle(page) -> list[str]:
    """Stop every tab's polls, close the other tabs, stop the page's fetch
    and XMLHttpRequest requests, block the rest, and drain what is out. A
    second settle of the page only drains.
    A page that polls more often than the drain's 50 ms check, such as the
    replay page's 250 ms `replay.message`, could otherwise start a request
    after the drain that met the database flush (a Postgres deadlock, #53).
    Returns the requests still out after the timeout."""
    if page.is_closed():
        return []
    for tab in page.context.pages:
        if tab != page:
            close_tab(page, tab)
    if not getattr(page, "blocker", None):
        stop_timers(page)
        stop_sending(page)
        _barrier(page)
        block_sending(page)
    return page.in_flight.drain(page)


# pytest-timeout's failure, "Timeout (>300.0s) from pytest-timeout.".
TIMED_OUT = re.compile(
    re.escape(pytest_timeout.PYTEST_FAILURE_MESSAGE).replace(re.escape("%s"), ".+")
)


def _timed_out(page, error: BaseException) -> bool:
    """Whether `error` is the browser timeout firing, and if so mark `page`
    so its teardown reads nothing from it. The timer fires once: a read of a
    page frozen in a script loop after that waits for ever. #250's fit() loop
    with no floor hung the settle's evaluate, and with the timer spent, the
    teardown's no_sideways_scroll hung too, until the renderer was killed
    (#250 retro)."""
    timed_out = isinstance(error, pytest.fail.Exception) and bool(
        TIMED_OUT.fullmatch(str(error))
    )
    if timed_out and page is not None:
        page.timed_out = True
    return timed_out


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """After the test body and before any fixture's teardown, stop every
    tab's polls and wait for its requests in flight. A test that ends on a
    click, or a poll that happens to be out, would otherwise get its reply
    after `settings` turned the test endpoints off (a 404) or the database
    was flushed."""
    page = item.funcargs.get("page")
    in_flight = getattr(page, "in_flight", None)
    try:
        result = yield
    except BaseException as error:
        if in_flight is not None and not _timed_out(page, error):
            try:
                _settle(page)
            except Exception:  # noqa: BLE001, S110 - the test's own error wins
                pass
        raise
    if in_flight is None:
        return result
    try:
        stuck = _settle(page)
    except BaseException as error:
        _timed_out(page, error)
        raise
    if in_flight.closed_with:
        raise AssertionError(
            f"a tab closed with requests out: {in_flight.closed_with}; "
            "close it with close_tab(page, tab)"
        )
    if stuck:
        raise AssertionError(f"still in flight after 10 s: {stuck}")
    return result


@pytest.fixture
def page(browser, live_server, size, page_errors):
    context = browser.new_context(**SIZES[size], base_url=live_server.url)
    context.add_init_script(UNBOUND_AFTER_SWAP)
    in_flight = InFlight(context)
    page = context.new_page()
    page.size = size
    page.in_flight = in_flight
    page.on("pageerror", lambda error: page_errors.add(f"pageerror: {error}"))
    page.on(
        "console",
        lambda msg: (
            msg.type == "error"
            and page_errors.console_error(msg.text, msg.location.get("url", ""))
        ),
    )

    def request_failed(request):
        if request.failure in BLOCKED and getattr(page, "blocker", None):
            return
        if (
            request.failure == BLOCKED[0]
            and request.url.endswith(SETTLE_BARRIER)
            and getattr(page, "barrier_sent", False)
        ):
            return
        page_errors.add(f"request failed: {request.url} {request.failure}")

    page.on("requestfailed", request_failed)

    def static_status(response):
        if "/static/" in response.url and response.status >= 400:
            page_errors.add(f"static {response.status}: {response.url}")

    page.on("response", static_status)
    # PHP's pages used alert() and confirm(). No new page may.
    page.on("dialog", lambda dialog: page_errors.add(f"dialog: {dialog.message}"))
    yield page
    try:
        if page.url.startswith("http") and not getattr(page, "timed_out", False):
            no_sideways_scroll(page)
    finally:
        context.close()
    errors = page_errors.final()
    # Every item, one per line: pytest's diff names only the first, and
    # tools/network_changed.py reruns a load run only when each item is a
    # changed network (#373).
    assert errors == [], f"{len(errors)} page errors, one per line:\n" + "\n".join(
        map(repr, errors)
    )


def log_in(page, role: str):
    """Log in through the login page, as a race crew would: tap the role,
    type the password, press Enter."""
    page.goto("/login/?all")
    page.locator(f'button[data-role="{role}"]').click()
    password = page.get_by_label("Password")
    password.fill(PASSWORDS[role])
    password.press("Enter")
    page.wait_for_url(re.compile(r"/$"))


@pytest.fixture
def login(page, event):
    """`login(role)`: the page, logged in as that default role, or left a
    visitor for "anonymous"."""

    def as_role(role: str):
        if role != "anonymous":
            log_in(page, role)
        return page

    return as_role


@contextmanager
def htmx_post(page, path: str):
    """Wait for the change the block makes: a POST to a URL holding `path`
    answers OK, and htmx has swapped and settled what it sent back."""
    with page.expect_response(
        lambda r: r.request.method == "POST" and path in r.url
    ) as reply:
        yield
    assert reply.value.ok, reply.value.status
    htmx_settled(page)


def shoot(page, name: str, full_page: bool = True) -> Path:
    """`full_page=False` for an open sheet, which is fixed to the screen."""
    no_sideways_scroll(page)
    path = SCREENSHOTS / f"{name}-{page.size}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path, full_page=full_page)
    return path


def no_sideways_scroll(page):
    width = page.evaluate("document.documentElement.scrollWidth")
    viewport = page.viewport_size["width"]
    assert width <= viewport, f"{page.url} is {width} px wide in {viewport} px"


def axe_violations(page) -> list[dict]:
    page.add_script_tag(path=str(AXE))
    result = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS
    )
    return [v for v in result["violations"] if v["impact"] in ("serious", "critical")]


def assert_accessible(page):
    violations = axe_violations(page)
    assert violations == [], [
        (v["id"], v["help"], [n["target"] for n in v["nodes"]]) for v in violations
    ]
